import json

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from fleet import (
    inventory_targets,
    notify_enrollment,
    notify_incidents,
    notify_status,
    notify_workload,
    send_slack,
    slack_webhook_url,
)
from review import CoverageError, collect_langfuse

from .conftest import ingest


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
                TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": name}]}],
            )
        targets = inventory_targets(ec2)
        assert {t["name"] for t in targets} == {"ordinary-untagged", "duplicate"}
        assert next(t for t in targets if t["name"] == "ordinary-untagged")["langfuse"] == {
            "environment": "ordinary-untagged"
        }
        duplicated = [t for t in targets if t["name"] == "duplicate"]
        assert len({t["slug"] for t in duplicated}) == 2 and all("langfuse" not in t for t in duplicated)


@pytest.mark.parametrize("failure", [(503, "busy", httpx.HTTPStatusError), (200, "invalid", CoverageError)])
def test_slack_only_announces_new_incidents_with_links_and_retries(store, target, failure, slack):
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        status, body = failure[:2] if len(attempts) == 1 else (200, "ok")
        return httpx.Response(status, text=body)

    with slack(handler):
        args = (
            store,
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


def test_slack_does_not_parse_workload_names_as_mentions(store, target, slack):
    renamed = {**target, "slug": "<@U123>"}
    store.sync([renamed], 1800, 1800)
    ingest(store, renamed)
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        return httpx.Response(200, text="ok")

    with slack(handler):
        notify_incidents(
            store,
            "https://incidents.example.test",
        )
    assert "on &lt;@U123&gt;" in attempts[0]["text"] and "<@U123>" not in attempts[0]["text"]


def test_incident_created_during_slack_delivery_is_not_skipped(store, target, slack):
    ingest(store, target)
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            ingest(store, target, 1800, anchor="observation:arrived-during-delivery")
        return httpx.Response(200, text="ok")

    with slack(handler):
        args = (
            store,
            "https://incidents.example.test",
        )
        assert notify_incidents(*args) == 1
        assert store.new_incidents()[0]["new_incidents"] == 1
        assert notify_incidents(*args) == 1
        assert notify_incidents(*args) == 0
    assert len(attempts) == 2 and all(item["text"].startswith("*1 new incident*") for item in attempts)


def test_enrollment_keeps_existing_acknowledgements_and_retries_new_deliveries(store, target, slack):
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        return httpx.Response(503 if len(attempts) == 1 else 200, text="busy" if len(attempts) == 1 else "ok")

    with slack(handler):
        args = (
            store,
            target,
            "https://incidents.example.test",
        )
        with pytest.raises(httpx.HTTPStatusError):
            notify_enrollment(*args)
        assert not store.notice("enrollment/" + target["key"])
        assert notify_enrollment(*args) == 1
        assert notify_enrollment(*args) == 0
        assert attempts[0] == attempts[1]
        assert f"<https://incidents.example.test/?workload={target['key']}|View workload>" in attempts[1]["text"]
        # Legacy deployments stored a fingerprint of the old message without a link.
        store.acknowledge("enrollment/" + target["key"], "legacy-fingerprint")
        assert notify_enrollment(*args) == 0 and len(attempts) == 2


@pytest.mark.parametrize("failure", [(503, "busy", httpx.HTTPStatusError), (200, "invalid", CoverageError)])
def test_status_reports_retry_and_advance_only_after_acknowledgement(store, target, failure, slack):
    attempts = []
    result = {
        "outcome": "completed",
        "window_end": 900,
        "report": {
            "activity": "alive",
            "progress": "Training is complete.",
            "quality": ["Validation is pending.", "Review the saved checks."],
            "milestones": ["Evaluation is next."],
            "alert": "Check the remaining budget.",
            "recommendation": "Review validation before continuing.",
        },
        "artifact_prefix": "private/assessment",
    }
    store.save_collection(target["key"], 900, {"target": target})
    store.publish(target["key"], 900, "status", result)

    def handler(request):
        attempts.append(json.loads(request.content))
        status, body = failure[:2] if len(attempts) == 1 else (200, "ok")
        return httpx.Response(status, text=body)

    with slack(handler):

        def send():
            return notify_status(
                store,
                store.fleet()[0],
                "https://incidents.example.test",
            )

        with pytest.raises(failure[2]):
            send()
        assert store.notice("status/" + target["key"]) is None
        assert send() == 1 and attempts[0] == attempts[1]
        assert send() == 0
        message = attempts[-1]["text"]
        assert message.startswith("*Status update* on experiment (i-test)")
        assert "*Window ending:* 1970-01-01T00:15:00+00:00" in message
        assert "*Progress:* Training is complete." in message
        assert "*Quality:* Validation is pending. Review the saved checks." in message
        assert "*Alert:* Check the remaining budget." in message
        assert "*Recommendation:* Review validation before continuing." in message
        assert f"/workloads/{target['key']}/status|View status>" in message
        assert "private/assessment" not in message
        # A new window sends even when the report is unchanged.
        store.save_collection(target["key"], 1800, {"target": target})
        store.publish(target["key"], 1800, "status", {**result, "window_end": 1800})
        assert send() == 1
        # Late publication of an older report cannot move the cursor backwards.
        store.save_collection(target["key"], 450, {"target": target})
        store.publish(target["key"], 450, "status", {**result, "window_end": 450})
        assert send() == 0 and len(attempts) == 3
        assert store.notice("status/" + target["key"]) == "1800"


def test_status_reports_escape_mentions_and_bound_verbose_text(store, target, slack):
    renamed = {**target, "slug": "<@U123>"}
    store.sync([renamed], 1800, 1800)
    store.save_collection(target["key"], 900, {"target": renamed})
    store.publish(
        target["key"],
        900,
        "status",
        {
            "outcome": "completed",
            "window_end": 900,
            "report": dict.fromkeys(
                ("activity", "progress", "quality", "milestones", "alert", "recommendation"),
                "<!channel> <@U123> & " * 1000,
            ),
        },
    )
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        return httpx.Response(200, text="ok")

    with slack(handler):
        assert (
            notify_workload(
                store,
                store.fleet()[0],
                "https://incidents.example.test",
            )
            == 1
        )
    message = attempts[0]["blocks"][0]["text"]["text"]
    assert "&lt;@U123&gt;" in message and "<@U123>" not in message
    assert "<!channel>" not in message and "&lt;!channel&gt;" in message
    assert len(message) <= 3000 and message.endswith("|View status>")


@pytest.mark.parametrize("state", ["stopping", "stopped", "shutting-down", "terminated", "no longer enrolled"])
def test_closure_retries_once_per_stop_and_enrollment_resumes_after_restart(store, target, state, slack):
    attempts = []
    store.acknowledge("enrollment/" + target["key"], "legacy-fingerprint")

    def handler(request):
        attempts.append(json.loads(request.content))
        return httpx.Response(503 if len(attempts) == 1 else 200, text="busy" if len(attempts) == 1 else "ok")

    with slack(handler):

        def send():
            return notify_workload(
                store,
                store.fleet()[0],
                "https://incidents.example.test",
            )

        store.sync([] if state == "no longer enrolled" else [{**target, "state": state}], 1800, 1800)
        with pytest.raises(httpx.HTTPStatusError):
            send()
        assert store.notice("enrollment/" + target["key"]) == "legacy-fingerprint"
        assert send() == 1 and attempts[0] == attempts[1]
        assert send() == 0
        assert f"Monitoring has ended on experiment (i-test). Instance state: {state}." in attempts[1]["text"]
        assert f"/?workload={target['key']}|View workload>" in attempts[1]["text"]
        store.sync([{**target, "state": "terminated"}], 2700, 1800)
        assert send() == 0
        store.sync([], 3600, 1800)
        assert send() == 0
        # Restarting can announce enrollment again without replaying old status.
        store.sync([target], 4500, 1800)
        assert (
            notify_enrollment(
                store,
                target,
                "https://incidents.example.test",
            )
            == 1
        )
        assert attempts[-1]["text"].startswith("Monitoring has begun")
        store.sync([], 5400, 1800)
        assert send() == 1 and send() == 0
        assert len(attempts) == 4


@pytest.mark.parametrize("state", ["running", "pending", "stopped", "terminated"])
def test_unassessed_instances_do_not_generate_lifecycle_notices(store, target, state, slack):
    store.sync([{**target, "state": state}], 1800, 1800)

    def handler(request):
        raise AssertionError("A never-enrolled instance must not send a notice")

    with slack(handler):
        assert (
            notify_workload(
                store,
                store.fleet()[0],
                "https://incidents.example.test",
            )
            == 0
        )


def test_slack_sender_loads_webhook_once_and_retries_configuration_failures(monkeypatch):
    attempts = []

    def handler(request):
        attempts.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, text="ok")

    slack_webhook_url.cache_clear()
    try:
        with mock_aws(), httpx.Client(transport=httpx.MockTransport(handler)) as http:
            monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
            monkeypatch.setattr("fleet.httpx.post", http.post)
            # A failed lookup must be retryable when configuration becomes available.
            with pytest.raises(ClientError):
                send_slack("First report")
            assert attempts == []
            ssm = boto3.client("ssm")
            first = "https://hooks.slack.com/services/test/fixture/first"
            for webhook, message in (
                (first, "First report"),
                ("https://hooks.slack.com/services/test/fixture/replacement", "Next report"),
            ):
                ssm.put_parameter(
                    Name="/crux/monitoring/env",
                    Type="SecureString",
                    Value=json.dumps({"MONITORING_SLACK_WEBHOOK_URL": webhook}),
                    Overwrite=True,
                )
                send_slack(message)
            assert [url for url, _ in attempts] == [first, first]
            assert [body["text"] for _, body in attempts] == ["First report", "Next report"]
    finally:
        slack_webhook_url.cache_clear()


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
