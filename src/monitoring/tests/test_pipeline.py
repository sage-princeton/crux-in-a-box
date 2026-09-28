import json
import time

import boto3
import httpx
import pytest
from moto import mock_aws

from review import digest
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
        registry = {"expires_at": int(time.time()) + 3600, "inference_budget_usd": 1,
                    "reviewer_models": ["google/gemini-example"], "targets": {instance: {
                        "authorization": "Inspect the fixture only", "subject_families": ["openai"]}}}
        s3.put_object(Bucket="monitoring-test", Key="config/registry.json", Body=json.dumps(registry))
        secrets = {"MONITORING_OPENROUTER_API_KEY": "test-only-inference-credential",
                   "MONITORING_SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/test/fixture/only"}
        boto3.client("ssm").put_parameter(Name="/crux/monitoring/test", Type="SecureString", Value=json.dumps(secrets))
        counts = {"model": 0, "slack": 0}
        report = {"summary": "fixture review", "workload_profile": "test workload", "next_source_ids": ["ec2:" + instance],
                  "coverage_gaps": [], "findings": [{"category": "test", "severity": "low", "confidence": "low",
                    "evidence": "fixture finding", "source_ids": ["ec2:" + instance], "benign_explanation": "a test"}]}

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
            assert "Review notes and intermediate artifacts" in request.content.decode()
            assert "fixture finding" not in request.content.decode()
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
        runtime.review(instance, end)
        assert runtime.state.get(key)["status"] == "done"
        assert counts == {"model": 1, "slack": 2}
        runtime.review(instance, end)
        assert counts == {"model": 1, "slack": 2}
        manifest = runtime.read_json(pending["artifact_prefix"] + "/manifest.json")
        assert len(manifest["artifacts"]) == 5
        for artifact in manifest["artifacts"]:
            value = runtime.read_json(artifact["key"])
            assert artifact["sha256"] == digest(value)
            assert artifact["version_id"]
            assert secrets["MONITORING_OPENROUTER_API_KEY"] not in json.dumps(value)
        runtime.http.close()
