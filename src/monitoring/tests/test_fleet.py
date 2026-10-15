import json

import boto3
import httpx
import pytest
from conftest import ingest
from moto import mock_aws

from fleet import inventory_targets, notify_enrollment, notify_incidents
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


@pytest.mark.parametrize(
    "failure", [(503, "busy", httpx.HTTPStatusError), (200, "invalid", CoverageError)]
)
def test_slack_only_announces_new_incidents_with_links_and_retries(store, target, failure):
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        status, body = failure[:2] if len(attempts) == 1 else (200, "ok")
        return httpx.Response(status, text=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        args = (
            http,
            store,
            "https://hooks.slack.com/services/test/fixture/only",
            "https://incidents.example.test",
        )
        assert notify_incidents(*args) == 0 and attempts == []
        item = ingest(store, target)
        with pytest.raises(failure[2]):
            notify_incidents(*args)
        assert store.new_incidents()[0]["new_incidents"] == 1
        assert notify_incidents(*args) == 1
        assert attempts[0] == attempts[1]
        message = attempts[-1]["blocks"][0]["text"]
        assert message["type"] == "mrkdwn"
        assert message["text"] == (
            "*1 new incident* :warning: on experiment\n"
            f"<https://incidents.example.test/?workload={target['key']}#incident-table|View incidents>"
        )
        store.transition(item["id"], item["version"], "closed", {"id": "operator"}, "Resolved")
        ingest(store, target, 1800)
        assert notify_incidents(*args) == 0 and len(attempts) == 2
        # Late evidence creates a new incident even when its review window is older.
        ingest(store, target, 450, anchor="observation:late")
        assert notify_incidents(*args) == 1
        ingest(store, target, 2700, anchor="observation:second")
        ingest(store, target, 3600, anchor="observation:third")
        assert notify_incidents(*args) == 1
        assert attempts[-1]["text"].startswith("*2 new incidents* :warning: on experiment\n")
        assert notify_incidents(*args) == 0 and len(attempts) == 4


def test_slack_does_not_parse_workload_names_as_mentions(store, target):
    renamed = {**target, "slug": "<@U123>"}
    store.sync([renamed], 1800, 1800)
    ingest(store, renamed)
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        return httpx.Response(200, text="ok")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        notify_incidents(
            http,
            store,
            "https://hooks.slack.com/services/test/fixture/only",
            "https://incidents.example.test",
        )
    assert "on &lt;@U123&gt;" in attempts[0]["text"] and "<@U123>" not in attempts[0]["text"]


def test_incident_created_during_slack_delivery_is_not_skipped(store, target):
    ingest(store, target)
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            ingest(store, target, 1800, anchor="observation:arrived-during-delivery")
        return httpx.Response(200, text="ok")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        args = (
            http,
            store,
            "https://hooks.slack.com/services/test/fixture/only",
            "https://incidents.example.test",
        )
        assert notify_incidents(*args) == 1
        assert store.new_incidents()[0]["new_incidents"] == 1
        assert notify_incidents(*args) == 1
        assert notify_incidents(*args) == 0
    assert len(attempts) == 2 and all(
        item["text"].startswith("*1 new incident*") for item in attempts
    )


def test_enrollment_keeps_existing_acknowledgements_and_retries_new_deliveries(store, target):
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
            target,
            "https://incidents.example.test",
        )
        with pytest.raises(httpx.HTTPStatusError):
            notify_enrollment(*args)
        assert not store.notice("enrollment/" + target["key"])
        assert notify_enrollment(*args) == 1
        assert notify_enrollment(*args) == 0
        assert attempts[0] == attempts[1]
        assert (
            f"<https://incidents.example.test/?workload={target['key']}|View workload>"
            in attempts[1]["text"]
        )
        # Legacy deployments stored a fingerprint of the old message without a link.
        store.acknowledge("enrollment/" + target["key"], "legacy-fingerprint")
        assert notify_enrollment(*args) == 0 and len(attempts) == 2


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
