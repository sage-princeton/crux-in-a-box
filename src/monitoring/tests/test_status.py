import json
import time
from pathlib import Path
from types import SimpleNamespace

import boto3
import httpx
import pytest
from moto import mock_aws

from review import CoverageError
from status_review import CHUNK_BYTES, MAX_CHUNKS, chunks
from status_store import StatusStore, workload_key
from status_worker import StatusRuntime, budget_line, validate


@pytest.fixture
def status_store():
    with mock_aws():
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="workload-status",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in ("pk", "sk")],
        )
        yield StatusStore(table)


def sample_report():
    return {
        "activity": "alive",
        "alert": "",
        "recommendation": "Review the next evaluation.",
        "progress": "Two evaluations completed.",
        "quality": "Insufficient evidence for a trend.",
        "milestones": "Baseline complete; next: validation.",
        "source_ids": ["logs:approved-status"],
    }


def completed(end, text="Two evaluations completed."):
    return {
        "outcome": "completed",
        "checked_at": end + 10,
        "window_end": end,
        "evidence_at": end - 1,
        "report": {**sample_report(), "progress": text, "budget": "Spend unknown; budget unknown."},
        "coverage_gaps": [],
        "models": [],
    }


def test_status_history_keeps_latest_success_and_handles_retry_order(status_store):
    key = workload_key("i-a", "run-1")
    status_store.sync(
        key,
        {"instance_id": "i-a", "workload_id": "run-1", "slug": "project", "state": "running"},
        1,
        1800,
    )
    status_store.publish(key, 900, completed(900))
    status_store.publish(
        key, 2700, {"outcome": "failed", "checked_at": 2710, "error": "Unavailable"}
    )
    status_store.publish(key, 1800, completed(1800, "Newer progress"))
    row = status_store.get("FLEET", key)
    assert row["attempt"] == "failed" and row["attempt_end"] == 2700
    assert row["latest"]["report"]["progress"] == "Newer progress"
    status_store.publish(key, 2700, completed(2700, "Recovered"))
    status_store.publish(key, 900, completed(900, "Must not replace the first result"))
    row = status_store.get("FLEET", key)
    assert row["attempt"] == "completed" and row["success_end"] == 2700
    history, _ = status_store.history(key)
    assert len(history) == 3
    assert history[-1]["report"]["progress"] == "Two evaluations completed."


def test_job_leases_budget_and_workload_identity_are_independent(status_store):
    first, second = workload_key("i-a", "run-1"), workload_key("i-a", "run-2")
    assert first != second
    assert status_store.claim(first, 900, "one", 1000)
    assert not status_store.claim(first, 900, "two", 1001)
    assert status_store.claim(second, 900, "two", 1001)
    status_store.release(first, 900, "one", completed=True)
    assert not status_store.claim(first, 900, "two", 9999)
    status_store.reserve(600_000, 1_000_000)
    with pytest.raises(CoverageError, match="exhausted"):
        status_store.reserve(600_000, 1_000_000)
    assert status_store.get("BUDGET", "inference")["reserved_microusd"] == 600_000


@pytest.fixture
def runtime(status_store, monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("STATUS_TABLE", status_store.table.name)
    monkeypatch.setenv("STATUS_BUCKET", "status-fixture")
    monkeypatch.setenv("STATUS_SECRETS_PARAMETER", "/crux/status/env")
    monkeypatch.setenv("STATUS_QUEUE", "status-queue")
    monkeypatch.setenv("STATUS_JOB", "status-job")
    # There is deliberately no incident table, incident config, or incident schedule.
    ec2 = boto3.client("ec2")
    iid = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)["Instances"][0][
        "InstanceId"
    ]
    ec2.create_tags(Resources=[iid], Tags=[{"Key": "Name", "Value": "project-alpha"}])
    end = int(time.time()) // 900 * 900
    logs = boto3.client("logs")
    logs.create_log_group(logGroupName="approved-status")
    logs.create_log_stream(logGroupName="approved-status", logStreamName="progress")
    logs.put_log_events(
        logGroupName="approved-status",
        logStreamName="progress",
        logEvents=[{"timestamp": (end - 1) * 1000, "message": "Two evaluations completed."}],
    )
    config = {
        "enabled": True,
        "expires_at": end + 86400,
        "inference_budget_usd": 10,
        "interval_seconds": 900,
        "stale_seconds": 1800,
        "sweep_model": "anthropic/claude-small",
        "summary_model": "anthropic/claude-large",
        "targets": {
            iid: {
                "workload_id": "experiment-1",
                "authorization": "Inspect approved exports only",
                "subject_families": ["openai"],
                "logs": [{"group": "approved-status", "streams": ["progress"]}],
            }
        },
    }
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="status-fixture")
    s3.put_object(Bucket="status-fixture", Key="config/status.json", Body=json.dumps(config))
    boto3.client("ssm").put_parameter(
        Name="/crux/status/env",
        Type="SecureString",
        Value=json.dumps({"MONITORING_OPENROUTER_API_KEY": "fixture-only-inference-secret"}),
    )
    result = StatusRuntime()
    result.submitted, result.calls = [], []
    result.batch = SimpleNamespace(submit_job=lambda **args: result.submitted.append(args))

    def model(request):
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": config[k],
                            "pricing": {"prompt": "0.000001", "completion": "0.000001"},
                        }
                        for k in ("sweep_model", "summary_model")
                    ]
                },
            )
        body = json.loads(request.content)
        result.calls.append(body)
        response = (
            {"notes": "Evaluations completed", "source_ids": ["logs:approved-status"]}
            if len(result.calls) % 2
            else sample_report()
        )
        return httpx.Response(
            200,
            json={
                "id": "generation-test",
                "model": body["model"],
                "usage": {"cost": 0.01},
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(response)}}
                ],
            },
        )

    result.http = httpx.Client(transport=httpx.MockTransport(model))
    yield result, iid, end
    result.http.close()


def test_pipeline_runs_without_incident_monitoring_and_deduplicates(runtime):
    worker, iid, end = runtime
    worker.discover()
    assert worker.submitted[0]["jobQueue"] == "status-queue"
    assert worker.submitted[0]["containerOverrides"]["command"][-3:] == [
        iid,
        "experiment-1",
        str(end),
    ]
    worker.check(iid, "experiment-1", end)
    worker.check(iid, "experiment-1", end)
    assert len(worker.calls) == 2
    row = worker.store.fleet()[0]
    assert row["latest"]["report"]["progress"] == "Two evaluations completed."
    assert row["latest"]["models"][0]["stage"] == "sweep"
    assert row["latest"]["models"][1]["stage"] == "summary"
    assert "budget unknown" in row["latest"]["report"]["budget"]
    assert boto3.client("dynamodb").list_tables()["TableNames"] == ["workload-status"]


def test_publication_retry_uses_saved_result_without_more_inference(runtime, monkeypatch):
    worker, iid, end = runtime
    worker.discover()
    original = worker.store.publish
    original_read = worker.read
    artifact_reads = []

    def read(key):
        artifact_reads.append(key)
        return original_read(key)

    monkeypatch.setattr(worker, "read", read)
    count = 0

    def publish(*args):
        nonlocal count
        count += 1
        if count == 1:
            raise RuntimeError("Temporary database failure")
        return original(*args)

    monkeypatch.setattr(worker.store, "publish", publish)
    with pytest.raises(CoverageError):
        worker.check(iid, "experiment-1", end)
    # A least-privilege S3 reader cannot probe absent objects without ListBucket.
    assert not artifact_reads
    worker.check(iid, "experiment-1", end)
    assert len(artifact_reads) == 1 and artifact_reads[0].endswith("/result.json")
    assert len(worker.calls) == 2
    assert worker.store.fleet()[0]["attempt"] == "completed"


def test_missing_evidence_reports_failure_and_keeps_previous_report(runtime):
    worker, iid, end = runtime
    worker.discover()
    worker.check(iid, "experiment-1", end)
    worker.logs.delete_log_group(logGroupName="approved-status")
    # Delete the durable checkpoint to exercise the collection path at another window.
    with pytest.raises(CoverageError, match="No recent"):
        worker.check(iid, "experiment-1", end - 900)
    assert len(worker.calls) == 2
    assert worker.store.fleet()[0]["latest"]["window_end"] == end


def test_retirement_and_changed_workload_do_not_run_queued_inference(runtime):
    worker, iid, end = runtime
    worker.ec2.stop_instances(InstanceIds=[iid])
    worker.discover()
    assert not worker.submitted
    assert worker.store.fleet()[0]["state"] == "stopped"
    worker.check(iid, "old-experiment", end)
    assert not worker.calls


def test_continuous_status_and_explicit_expiry(runtime):
    worker, _, end = runtime
    worker.config["expires_at"] = 0
    validate(worker.config)
    assert worker.active(end + 10 * 365 * 86400)
    worker.config["enabled"] = False
    assert not worker.active(end)
    worker.config["enabled"] = True
    worker.config["expires_at"] = end
    assert worker.active(end - 1)
    assert not worker.active(end)
    worker.config["expires_at"] = -1
    with pytest.raises(ValueError, match="expiry"):
        validate(worker.config)


def test_absent_workload_keeps_its_name_without_submitting_checks(runtime):
    worker, iid, _ = runtime
    worker.config["targets"][iid]["name"] = "retired-project"
    worker.ec2.terminate_instances(InstanceIds=[iid])
    # EC2 eventually stops returning terminated instances; select an absent ID.
    worker.config["targets"]["i-00000000000000000"] = worker.config["targets"].pop(iid)
    worker.discover()
    assert not worker.submitted
    row = worker.store.fleet()[0]
    assert row["slug"] == "retired-project" and row["state"] == "no longer present"


def test_production_status_configuration_is_accepted_by_the_worker():
    config = json.loads(
        Path(__file__).resolve().parents[1].joinpath("status.production.json").read_text()
    )
    validate(config)


def test_budget_exhaustion_prevents_model_calls(runtime):
    worker, iid, end = runtime
    worker.discover()
    worker.store.reserve(10_000_000, 10_000_000)
    with pytest.raises(CoverageError, match="budget exhausted"):
        worker.check(iid, "experiment-1", end)
    assert not worker.calls
    assert worker.store.fleet()[0]["attempt"] == "failed"


def test_discovery_requeues_crashed_jobs_after_their_lease_expires(runtime):
    worker, iid, end = runtime
    key = workload_key(iid, "experiment-1")
    assert worker.store.claim(key, end - 1800, "crashed", end - 1800)
    worker.discover()
    windows = {int(job["containerOverrides"]["command"][-1]) for job in worker.submitted}
    assert windows == {end - 1800, end}


def test_invented_evidence_and_provider_auth_failures_are_visible(runtime):
    worker, iid, end = runtime
    worker.discover()
    original = worker.http._transport

    def bad_citation(request):
        response = original.handle_request(request)
        if request.url.path.endswith("completions"):
            body = json.loads(response.read())
            result = json.loads(body["choices"][0]["message"]["content"])
            result["source_ids"] = ["invented"]
            body["choices"][0]["message"]["content"] = json.dumps(result)
            return httpx.Response(200, json=body)
        return response

    worker.http = httpx.Client(transport=httpx.MockTransport(bad_citation))
    with pytest.raises(CoverageError, match="not supplied"):
        worker.check(iid, "experiment-1", end)
    worker.http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401)))
    with pytest.raises(CoverageError):
        worker.check(iid, "experiment-1", end)
    assert worker.store.get("HEALTH", "inference")["blocked"] is True


def test_evidence_chunks_disclose_omissions_and_keep_source_identity():
    sources = [{"id": "file:/exports/large.txt", "data": "a" * (CHUNK_BYTES * 20), "kind": "sftp"}]
    batches, gaps = chunks(sources)
    assert len(batches) == MAX_CHUNKS and gaps
    assert all(len(json.dumps(batch).encode()) < CHUNK_BYTES for batch in batches)
    assert {source["id"] for batch in batches for source in batch} == {sources[0]["id"]}


def test_config_and_budget_baselines_do_not_invent_metrics(runtime):
    worker, _, end = runtime
    validate(worker.config)
    assert "Spend unknown" in budget_line({"budget_usd": 20}, end)
    assert "Recorded $5.00" in budget_line({"spent_usd": 5, "spend_as_of": end - 60}, end)
    worker.config["summary_model"] = "openai/gpt-example"
    with pytest.raises(ValueError, match="same permitted family"):
        validate(worker.config)
