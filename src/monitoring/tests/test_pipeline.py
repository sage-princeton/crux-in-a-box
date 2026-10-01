import hashlib
import json
import time

import boto3
import httpx
import pytest
from moto import mock_aws

from worker import Runtime


def test_slack_retry_uses_durable_evidence_without_repeating_inference(monkeypatch):
    with mock_aws():
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        monkeypatch.setenv("MONITORING_BUCKET", "monitoring-test")
        monkeypatch.setenv("MONITORING_TABLE", "monitoring-test")
        monkeypatch.setenv("MONITORING_SECRETS_PARAMETER", "/crux/monitoring/test")
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="monitoring-test")
        s3.put_bucket_versioning(Bucket="monitoring-test", VersioningConfiguration={"Status": "Enabled"})
        boto3.client("dynamodb").create_table(TableName="monitoring-test", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}])
        instance = boto3.client("ec2").run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)["Instances"][0]["InstanceId"]
        end = int(time.time()) // 300 * 300
        logs = boto3.client("logs")
        logs.create_log_group(logGroupName="approved-evidence")
        logs.create_log_stream(logGroupName="approved-evidence", logStreamName="activity")
        logs.put_log_events(logGroupName="approved-evidence", logStreamName="activity",
                            logEvents=[{"timestamp": (end - 1) * 1000, "message": "fixture activity"}])
        registry = {"expires_at": int(time.time()) + 3600, "inference_budget_usd": 1,
                    "reviewer_models": ["google/gemini-example"], "targets": {instance: {
                        "authorization": "Inspect the fixture only", "subject_families": ["openai"],
                        "logs": [{"group": "approved-evidence", "streams": ["activity"]}]}}}
        s3.put_object(Bucket="monitoring-test", Key="config/registry.json", Body=json.dumps(registry))
        secrets = {"MONITORING_OPENROUTER_API_KEY": "test-only-inference-credential",
                   "MONITORING_SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test/fixture/only"}
        boto3.client("ssm").put_parameter(Name="/crux/monitoring/test", Type="SecureString", Value=json.dumps(secrets))
        counts = {"model": 0, "slack": 0}
        report = {"summary": "fixture review", "workload_profile": "test workload", "next_source_ids": ["ec2:" + instance],
                  "coverage_gaps": [], "findings": [{"category": "test", "detector_id": "other", "anchor_id": "ec2:" + instance, "severity": "low", "confidence": "low",
                    "evidence": "fixture finding test-only-inference-credential", "source_ids": ["ec2:" + instance], "benign_explanation": "a test"}]}

        def handler(request):
            if request.url.path == "/api/v1/models":
                return httpx.Response(200, json={"data": [{"id": "google/gemini-example",
                    "pricing": {"prompt": "0.000001", "completion": "0.000001"}}]})
            if request.url.path == "/api/v1/chat/completions":
                counts["model"] += 1
                return httpx.Response(200, json={"model": "google/gemini-example", "choices": [{
                    "finish_reason": "stop", "message": {"content": json.dumps(report)}}]})
            assert request.url.host == "hooks.slack.com"
            counts["slack"] += 1
            assert "Full report and evidence" in request.content.decode()
            assert "fixture finding" in request.content.decode()
            assert secrets["MONITORING_OPENROUTER_API_KEY"] not in request.content.decode()
            return httpx.Response(503 if counts["slack"] == 1 else 200, text="busy" if counts["slack"] == 1 else "ok")

        runtime = Runtime()
        runtime.http.close()
        runtime.http = httpx.Client(transport=httpx.MockTransport(handler))
        end = int(time.time()) // 300 * 300
        with pytest.raises(httpx.HTTPStatusError):
            runtime.review(instance, end)
        key = f"REVIEW#{instance}#{end}"
        pending = runtime.state.get(key)
        assert pending["status"] == "pending_notification"
        history = s3.get_object(Bucket="monitoring-test", Key="reviews/incidents/index.html")["Body"].read()
        assert b"fixture finding" in history
        runtime.review(instance, end)
        assert runtime.state.get(key)["status"] == "done"
        assert counts == {"model": 1, "slack": 2}
        runtime.review(instance, end)
        assert counts == {"model": 1, "slack": 2}
        manifest = runtime.read_json(pending["artifact_prefix"] + "/manifest.json")
        assert len(manifest["artifacts"]) == 6
        for artifact in manifest["artifacts"]:
            obj = s3.get_object(Bucket="monitoring-test", Key=artifact["key"], VersionId=artifact["version_id"])
            stored = obj["Body"].read()
            assert artifact["sha256"] == hashlib.sha256(stored).hexdigest()
            assert artifact["version_id"]
            assert secrets["MONITORING_OPENROUTER_API_KEY"] not in stored.decode()
            if artifact["key"].endswith("report.md"):
                assert obj["ContentType"] == "text/markdown; charset=utf-8"
                assert stored.decode().startswith("# Monitoring review\n")
                assert "fixture finding" in stored.decode() and "Possible explanation: a test" in stored.decode()
        runtime.http.close()
