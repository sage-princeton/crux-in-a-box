import json

import boto3
import httpx
import pytest
from moto import mock_aws

from fleet import inventory_targets, notify
from review import CoverageError, collect_langfuse


def test_every_ec2_is_enrolled_except_the_two_named_monitoring_exclusions():
    with mock_aws():
        ec2 = boto3.client("ec2", region_name="us-east-1")
        for name in (
            "crux-control",
            "crux-monitor-worker",
            "ordinary-untagged",
            "duplicate",
            "duplicate",
        ):
            ec2.run_instances(
                ImageId="ami-12345678",
                MinCount=1,
                MaxCount=1,
                TagSpecifications=[
                    {"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": name}]}
                ],
            )
        targets = inventory_targets(ec2)
        assert {t["name"] for t in targets} == {"ordinary-untagged", "duplicate"}
        assert next(t for t in targets if t["name"] == "ordinary-untagged")["langfuse"] == {
            "environment": "ordinary-untagged"
        }
        duplicated = [t for t in targets if t["name"] == "duplicate"]
        assert len({t["slug"] for t in duplicated}) == 2 and all(
            "langfuse" not in t for t in duplicated
        )


def test_slack_failures_retry_and_success_is_acknowledged(store):
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        return httpx.Response(
            503 if len(attempts) == 1 else 200, text="busy" if len(attempts) == 1 else "ok"
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        args = (
            http,
            store,
            "https://hooks.slack.com/services/test/fixture/only",
            "enrollment/test",
            "Monitoring has begun on @here",
        )
        with pytest.raises(httpx.HTTPStatusError):
            notify(*args)
        assert store.notice("enrollment/test") is None
        assert notify(*args) == "sent"
        assert notify(*args) == "suppressed"
    assert len(attempts) == 2
    assert attempts[-1]["blocks"][0]["text"]["type"] == "plain_text"


def test_langfuse_does_not_accept_another_runs_observations():
    secrets = {
        "MONITORING_LANGFUSE_BASE_URL": "https://langfuse.example",
        "MONITORING_LANGFUSE_PUBLIC_KEY": "public",
        "MONITORING_LANGFUSE_SECRET_KEY": "secret",
    }

    def handler(request):
        assert request.url.params.get_list("environment") == ["experiment"]
        return httpx.Response(200, json={"data": [{"id": "1", "environment": "other"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(CoverageError, match="unexpected"):
            collect_langfuse(http, {"environment": "experiment"}, secrets, 0, 900)
