import json
import time

import boto3
import httpx
import pytest
from moto import mock_aws

from fleet import notify_incidents, notify_workload
from worker import Runtime

MODEL_CATALOG = {
    "data": [
        {"id": model, "pricing": {"prompt": "0.000001", "completion": "0.000001"}}
        for model in ("anthropic/claude-haiku-4.5", "anthropic/claude-sonnet-4.6")
    ]
}


@pytest.fixture
def runtime(store, target, monkeypatch):
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        monkeypatch.setenv("MONITORING_BUCKET", "monitoring-test")
        monkeypatch.setenv("MONITORING_WORKSPACE_DOCUMENT", "copy-workspace")
        monkeypatch.setenv("MONITORING_WEB_ORIGIN", "https://incidents.example.test")
        monkeypatch.setenv(
            "MONITORING_UPLOAD_ROLE", "arn:aws:iam::123456789012:role/workspace-upload"
        )
        boto3.client("s3").create_bucket(Bucket="monitoring-test")
        secrets = {
            "MONITORING_OPENROUTER_API_KEY": "test-only-inference-credential",
            "MONITORING_SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test/fixture/only",
        }
        counts = {"collection": 0, "model": 0, "slack": 0}

        def handler(request):
            if request.url.path == "/api/v1/models":
                return httpx.Response(200, json=MODEL_CATALOG)
            if request.url.host == "hooks.slack.com":
                counts["slack"] += 1
                return httpx.Response(
                    503 if counts["slack"] == 1 else 200,
                    text="busy" if counts["slack"] == 1 else "ok",
                )
            counts["model"] += 1
            payload = json.loads(request.content)
            if payload["response_format"]["json_schema"]["name"] == "project_status":
                # Status failure must not block the independent incident assessment.
                return httpx.Response(503, json={"error": "temporary"})
            report = {
                "summary": "Reviewed",
                "workload_profile": "Test run",
                "next_source_ids": [],
                "findings": [
                    {
                        "category": "Test",
                        "detector_id": "other",
                        "anchor_id": "observation:123",
                        "severity": "low",
                        "confidence": "medium",
                        "evidence": "Test-only evidence",
                        "source_ids": ["observation:123"],
                        "benign_explanation": "Fixture",
                        "what_happened": "Test-only evidence",
                        "why_it_happened": "A fixture reported this activity.",
                        "next_steps": "Inspect the test fixture.",
                    }
                ],
                "coverage_gaps": [],
            }
            return httpx.Response(
                200,
                json={
                    "model": payload["model"],
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(report)}}
                    ],
                },
            )

        result = Runtime(store)
        result.http.close()
        result.http = httpx.Client(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(
            "fleet.slack_webhook_url", lambda: secrets["MONITORING_SLACK_WEBHOOK_URL"]
        )
        monkeypatch.setattr(
            "fleet.httpx.post", lambda *args, **kwargs: result.http.post(*args, **kwargs)
        )

        def collect(*args):
            counts["collection"] += 1
            return {
                "sources": [
                    {"id": "observation:123", "kind": "langfuse", "data": {"model": "gpt-6.1-sol"}}
                ],
                "gaps": [],
                "workspace": {"key": "whole-workspace.tar.gz", "sha256": "fixture"},
                "captured_at": int(time.time()),
            }

        result.collector.collect = collect
        yield result, target, secrets, counts
        result.http.close()


def test_one_collection_and_independent_assessments_survive_slack_retry(runtime):
    worker, target, secrets, counts = runtime
    worker.run_target(target, 900, {}, secrets)
    workload_args = (
        worker.store,
        worker.store.fleet()[0],
        "https://incidents.example.test",
    )
    with pytest.raises(httpx.HTTPStatusError):
        notify_workload(*workload_args)
    assert worker.store.assessment(target["key"], 900, "status")["outcome"] == "failed"
    assert worker.store.assessment(target["key"], 900, "incident")["outcome"] == "completed"
    assert counts["collection"] == 1 and counts["slack"] == 1
    args = (
        worker.store,
        "https://incidents.example.test",
    )
    initial = counts.copy()
    worker.run_target(target, 900, {}, secrets)
    assert notify_workload(*workload_args) == 1
    assert worker.store.notice("enrollment/" + target["key"])
    assert notify_incidents(*args) == 1
    assert counts["model"] == initial["model"] and counts["collection"] == 1
    delivered = counts.copy()
    worker.run_target(target, 900, {}, secrets)
    assert notify_workload(*workload_args) == 0
    assert notify_incidents(*args) == 0
    assert counts == delivered
    assert len(worker.store.instance_page(target["instance_id"], None, None, 50)) == 1


def test_model_checkpoint_is_reused_after_database_publication_failure(runtime, monkeypatch):
    worker, target, secrets, counts = runtime
    publish = worker.store.publish

    def fail_status(key, end, kind, result):
        if kind == "status":
            raise RuntimeError("Publication failed")
        return publish(key, end, kind, result)

    monkeypatch.setattr(worker.store, "publish", fail_status)
    worker.run_target(target, 900, {}, secrets)
    initial = counts.copy()
    assert worker.store.assessment(target["key"], 900, "status") is None
    pending = worker.store.pending(1800)
    assert pending[0]["evidence"]["target"]["instance_id"] == target["instance_id"]
    monkeypatch.setattr(worker.store, "publish", publish)

    # Even if the run has disappeared, the saved archive is the evidence for this window.
    def unavailable_run(*args):
        raise AssertionError("must reuse snapshot")

    monkeypatch.setattr(worker.collector, "collect", unavailable_run)
    worker.run_target(pending[0]["evidence"]["target"], 900, {}, secrets)
    worker.run_target(target, 900, {}, secrets)
    assert worker.store.pending(1800) == []
    assert counts["model"] == initial["model"] and counts["collection"] == 1


def test_poll_retries_closure_without_reenrolling_an_archived_target(runtime, monkeypatch):
    worker, target, secrets, counts = runtime
    worker.store.acknowledge("enrollment/" + target["key"], "legacy-enrollment")
    publish = worker.store.publish

    def fail_status(key, end, kind, result):
        if kind == "status":
            raise RuntimeError("Publication failed")
        return publish(key, end, kind, result)

    monkeypatch.setattr(worker.store, "publish", fail_status)
    worker.run_target(target, 900, {}, secrets)
    initial = counts.copy()
    monkeypatch.setattr(worker.store, "publish", publish)
    monkeypatch.setattr(worker, "secrets", lambda: secrets)
    monkeypatch.setattr("worker.time.time", lambda: 1801)
    messages = []

    def handler(request):
        assert request.url.host == "hooks.slack.com", "Must reuse saved model results"
        assert str(request.url) == secrets["MONITORING_SLACK_WEBHOOK_URL"]
        messages.append(json.loads(request.content))
        return httpx.Response(
            503 if len(messages) == 1 else 200, text="busy" if len(messages) == 1 else "ok"
        )

    worker.http.close()
    worker.http = httpx.Client(transport=httpx.MockTransport(handler))
    # The fixture's instance is absent from EC2; its saved target still says running.
    worker.tick()
    assert worker.store.pending(1801) == []
    assert worker.store.fleet()[0]["state"] == "no longer enrolled"
    assert worker.store.notice("enrollment/" + target["key"]) == "legacy-enrollment"
    assert messages[0]["text"].startswith("Monitoring has ended")
    assert messages[1]["text"].startswith("*1 new incident*")
    worker.tick()
    assert messages[0] == messages[2]
    assert worker.store.notice("enrollment/" + target["key"]) == "closed"
    worker.tick()
    assert len(messages) == 3
    assert counts["model"] == initial["model"] and counts["collection"] == 1


@pytest.mark.parametrize("verbose", [False, True])
def test_successful_status_sweep_summary_and_incident_publish_independently(
    runtime, monkeypatch, verbose
):
    worker, target, secrets, counts = runtime
    calls, messages = [], []
    collect = worker.collector.collect

    def workspace_evidence(*args):
        evidence = collect(*args)
        evidence["sources"].append(
            {
                "id": "workspace:" + "a" * 32,
                "kind": "workspace",
                "data": "".join(f"{index:06}\n" for index in range(500)),
            }
        )
        evidence["sources"].extend(
            {
                "id": f"observation:{index}",
                "kind": "langfuse",
                "data": {"model": "gpt-6.1-sol", "input": "Evidence. " * 256},
            }
            for index in range(12)
        )
        return evidence

    monkeypatch.setattr(worker.collector, "collect", workspace_evidence)

    def handler(request):
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json=MODEL_CATALOG)
        if request.url.host == "hooks.slack.com":
            messages.append((str(request.url), json.loads(request.content)))
            return httpx.Response(200, text="ok")
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]
        calls.append(schema["name"])
        if schema["name"] == "project_status":
            allowed = json.loads(body["messages"][1]["content"])["allowed_source_ids"]
        if schema["name"] == "project_status" and "notes" in schema["schema"]["properties"]:
            report = {
                "notes": "Work is active. " * (150 if verbose else 1),
                "source_ids": allowed,
            }
        elif schema["name"] == "project_status":
            report = {
                "activity": "alive",
                "alert": "",
                "recommendation": "Continue.",
                "progress": "Work is active. " * (20 if verbose else 1),
                "quality": ["Not established."],
                "milestones": [],
                "details": [],
                "reported_budget": "Workspace ledger reports $200.49 spent against a $200 limit.",
                "source_ids": allowed,
            }
        else:
            report = {
                "summary": "No findings.",
                "workload_profile": "Test run",
                "next_source_ids": [],
                "findings": [],
                "coverage_gaps": [],
            }
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(report)}}],
            },
        )

    worker.http.close()
    worker.http = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("worker.inventory_targets", lambda ec2: [target])
    monkeypatch.setattr("worker.time.time", lambda: 901)
    monkeypatch.setattr(worker, "secrets", lambda: secrets)
    worker.tick()
    assert len(messages) == 3
    assert all(url == secrets["MONITORING_SLACK_WEBHOOK_URL"] for url, _ in messages)
    assert messages[0][1]["text"].startswith("Monitoring has begun")
    assert "*Progress:* Work is active." in messages[1][1]["text"]
    assert messages[2][1]["text"].startswith("*1 new incident*")
    initial = calls.copy()
    assert worker.store.fleet()[0]["latest"]["report"]["activity"] == "alive"
    status = worker.store.assessment(target["key"], 900, "status")
    assert len(status["report"]["source_ids"]) == 14
    assert status["report"]["progress"] == "Work is active. " * (20 if verbose else 1)
    assert status["report"]["quality"] == ["Not established."]
    assert status["report"]["reported_budget"].startswith("Workspace ledger reports")
    assert "budget" not in status["report"]
    assert status["subject_families"] == ["openai"]
    incident = worker.store.assessment(target["key"], 900, "incident")
    assert incident["outcome"] == "completed"
    assert any("workspace event anchors" in gap for gap in incident["report"]["coverage_gaps"])
    worker.tick()
    assert len(messages) == 3
    assert counts["collection"] == 1 and calls == initial
    target["state"] = "stopped"
    worker.tick()
    assert messages[-1][1]["text"].startswith("Monitoring has ended")
    target["state"] = "running"
    worker.tick()
    assert messages[-1][1]["text"].startswith("Monitoring has begun")
    target["state"] = "stopped"
    worker.tick()
    worker.tick()
    assert len(messages) == 6 and messages[-1][1]["text"].startswith("Monitoring has ended")
    assert counts["collection"] == 1 and calls == initial


def test_idle_status_uses_prior_observed_family_and_preserves_resource_evidence(
    runtime, monkeypatch
):
    worker, target, secrets, counts = runtime
    key = target["key"]
    target["subject_families"] = []
    # A report from before family provenance was added must bootstrap from its saved evidence.
    worker.store.save_collection(
        key,
        450,
        {"sources": [{"kind": "langfuse", "data": {"model": "gpt-6.1-sol"}}]},
    )
    worker.store.publish(
        key, 450, "status", {"outcome": "completed", "window_end": 450, "report": {}}
    )
    resource_text = (
        "The workspace ledger records LLM spend of $200.49 against $200 as of October 11. "
        "The task deadline is November 18, 2026, 23:36 UTC."
    )
    source = {"id": "workspace:ledger", "kind": "workspace", "data": resource_text, "mtime": 800}
    monkeypatch.setattr(
        worker.collector,
        "collect",
        lambda *args: {
            "sources": [source],
            "gaps": ["Langfuse returned no observations for this collection window."],
            "workspace": {},
            "langfuse_status": "empty",
        },
    )

    def handler(request):
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json=MODEL_CATALOG)
        if request.url.host == "hooks.slack.com":
            return httpx.Response(200, text="ok")
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        assert source in payload["sources"]
        assert body["model"].startswith("anthropic/")
        report = (
            {"notes": resource_text, "source_ids": [source["id"]]}
            if "notes" in body["response_format"]["json_schema"]["schema"]["properties"]
            else {
                "activity": "stalled",
                "alert": "",
                "recommendation": "Review the partial outcome.",
                "progress": "No new agent activity was observed.",
                "quality": [],
                "milestones": [],
                "details": [],
                "reported_budget": resource_text,
                "source_ids": [source["id"]],
            }
        )
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(report)}}],
            },
        )

    worker.http.close()
    worker.http = httpx.Client(transport=httpx.MockTransport(handler))
    for end in (900, 1800):
        worker.run_target(target, end, {}, secrets)
        result = worker.store.assessment(key, end, "status")
        assert result["outcome"] == "completed"
        assert result["subject_families"] == ["openai"]
        assert result["report"]["reported_budget"] == resource_text
        assert "budget" not in result["report"]
        assert result["evidence_at"] == 800
        assert any("no observations" in gap for gap in result["coverage_gaps"])
    # New same-family or unidentified observations must still block the assessment.
    for end, model in ((2700, "claude-example"), (3600, "unrecognized-model")):
        observed = {"id": "observation:new", "kind": "langfuse", "data": {"model": model}}
        evidence = worker.collector.collect()
        evidence["sources"] = [source, observed]
        evidence["langfuse_status"] = "collected"
        worker.store.save_collection(key, end, evidence)
        result = worker.assess(target, end, "status", evidence, {}, secrets)
        assert result["outcome"] == "failed"
        assert "different model family" in result["error"] or "unknown" in result["error"]
        assert "error_code" not in result


@pytest.mark.parametrize("langfuse_status", ["empty", "unavailable", "unmapped", None])
def test_only_empty_langfuse_without_known_family_is_a_recency_failure(runtime, langfuse_status):
    worker, target, secrets, counts = runtime
    target["subject_families"] = []
    evidence = {"sources": [], "gaps": [], "workspace": {}, "langfuse_status": langfuse_status}
    worker.store.save_collection(target["key"], 900, evidence)
    result = worker.assess(
        target,
        900,
        "status",
        evidence,
        {},
        secrets,
    )
    assert result["outcome"] == "failed"
    if langfuse_status == "empty":
        assert result["error_code"] == "langfuse_recency"
        assert "Langfuse logs are stale or have not been updated" in result["error"]
        assert worker.store.fleet()[0]["error_code"] == "langfuse_recency"
    else:
        assert "error_code" not in result
        assert "Subject model family is unknown" in result["error"]
    assert counts["model"] == 0


@pytest.mark.parametrize("other_run", [False, True])
def test_status_cannot_borrow_subject_identity_from_another_run_or_future_window(
    runtime, other_run
):
    worker, target, secrets, counts = runtime
    target["subject_families"] = []
    prior_target = {**target, "instance_id": "i-other"} if other_run else target
    if other_run:
        worker.store.sync([target, prior_target], 900, 1800)
    prior_end = 450 if other_run else 1800
    worker.store.save_collection(prior_target["key"], prior_end, {"sources": []})
    worker.store.publish(
        prior_target["key"],
        prior_end,
        "status",
        {"outcome": "completed", "window_end": prior_end, "subject_families": ["openai"]},
    )
    evidence = {"sources": [], "gaps": [], "workspace": {}, "langfuse_status": "empty"}
    worker.store.save_collection(target["key"], 900, evidence)
    result = worker.assess(target, 900, "status", evidence, {}, secrets)
    assert result["outcome"] == "failed"
    assert result["error_code"] == "langfuse_recency"
    assert counts["model"] == 0
