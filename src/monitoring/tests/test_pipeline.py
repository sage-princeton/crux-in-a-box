import json
import time

import boto3
import httpx
import pytest
from moto import mock_aws

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
    with pytest.raises(httpx.HTTPStatusError):
        worker.run_target(target, 900, {}, secrets)
    assert worker.store.assessment(target["key"], 900, "status")["outcome"] == "failed"
    assert worker.store.assessment(target["key"], 900, "incident")["outcome"] == "completed"
    assert counts["collection"] == 1
    assert worker.store.notice("enrollment/" + target["key"]) is None
    initial = counts.copy()
    worker.run_target(target, 900, {}, secrets)
    assert worker.store.notice("enrollment/" + target["key"])
    assert counts["model"] == initial["model"] and counts["collection"] == 1
    delivered = counts.copy()
    worker.run_target(target, 900, {}, secrets)
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
    with pytest.raises(httpx.HTTPStatusError):
        worker.run_target(pending[0]["evidence"]["target"], 900, {}, secrets)
    worker.run_target(target, 900, {}, secrets)
    assert worker.store.pending(1800) == []
    assert counts["model"] == initial["model"] and counts["collection"] == 1


def test_successful_status_sweep_summary_and_incident_publish_independently(runtime):
    worker, target, secrets, counts = runtime
    calls = []

    def handler(request):
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json=MODEL_CATALOG)
        if request.url.host == "hooks.slack.com":
            return httpx.Response(200, text="ok")
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]
        calls.append(schema["name"])
        if schema["name"] == "project_status" and "notes" in schema["schema"]["properties"]:
            report = {"notes": "Work is active.", "source_ids": ["observation:123"]}
        elif schema["name"] == "project_status":
            report = {
                "activity": "alive",
                "alert": "",
                "recommendation": "Continue.",
                "progress": "Work is active.",
                "quality": "Not established.",
                "milestones": "Unknown.",
                "source_ids": ["observation:123"],
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
    worker.run_target(target, 900, {}, secrets)
    initial = calls.copy()
    assert worker.store.fleet()[0]["latest"]["report"]["activity"] == "alive"
    assert worker.store.assessment(target["key"], 900, "incident")["outcome"] == "completed"
    worker.run_target(target, 900, {}, secrets)
    assert counts["collection"] == 1 and calls == initial
