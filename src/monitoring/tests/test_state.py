from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from review import CoverageError
from worker import LEASE_SECONDS, Runtime, State


@pytest.fixture
def state():
    with mock_aws():
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="monitoring",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in ("pk", "sk")],
        )
        yield State(table)


def test_lease_fences_stale_workers_and_completed_windows(state):
    key = "REVIEW#i-example#300"
    assert state.claim(key, "first", 1000)
    assert not state.claim(key, "second", 1001)
    assert state.claim(key, "second", 1001 + LEASE_SECONDS)
    with pytest.raises(ClientError):
        state.save(key, "first", {"status": "done"})
    state.save(key, "second", {"status": "done"}, release=True)
    assert not state.claim(key, "third", 3000 + LEASE_SECONDS)


def test_delivery_retry_keeps_saved_artifact_reference(state):
    key = "REVIEW#i-example#300"
    assert state.claim(key, "first", 1000)
    state.save(
        key,
        "first",
        {"status": "pending_notification", "artifact_prefix": "reviews/saved"},
        release=True,
    )
    assert state.claim(key, "retry", 1001)
    item = state.get(key)
    assert item["status"] == "pending_notification"
    assert item["artifact_prefix"] == "reviews/saved"


def test_discovery_persists_target_before_queue_and_retries_absent_workload(state, monkeypatch):
    runtime = Runtime.__new__(Runtime)
    runtime.state = state
    runtime.config = {"expires_at": 9999, "fleet": {"exclude_names": ["crux-control"]}}
    target = {
        "authorization": "approved",
        "langfuse": {"environment": "experiment"},
        "service_worker": False,
    }
    monkeypatch.setenv("MONITORING_QUEUE", "queue")
    monkeypatch.setenv("MONITORING_REVIEW_JOB", "review")
    monkeypatch.setattr("worker.time.time", lambda: 600)
    commands = []

    def submit(**kwargs):
        command = kwargs["containerOverrides"]["command"]
        if command[2] == "review":
            assert state.get(f"REVIEW#{command[3]}#{command[4]}")["target"] == target
        commands.append(command)

    runtime.batch = SimpleNamespace(submit_job=submit)
    runtime.put = lambda *args: None
    runtime.inventory = lambda **kwargs: ({"i-example": target}, [], set())
    # Simulate the index immediately exposing the newly created pending row.
    state.pending = lambda: [state.get("REVIEW#i-example#600")]
    runtime.discover()
    assert sum(c[2] == "review" for c in commands) == 1
    commands.clear()
    monkeypatch.setattr("worker.time.time", lambda: 900)
    runtime.inventory = lambda **kwargs: ({}, [], set())
    runtime.discover()
    assert ["python", "worker.py", "review", "i-example", "600"] in commands


def test_budget_reservations_stop_at_limit(state):
    with pytest.raises(CoverageError):
        state.reserve(101, 100)
    state.reserve(60, 100)
    with pytest.raises(CoverageError):
        state.reserve(41, 100)
    state.reserve(40, 100)
    assert state.get("BUDGET#inference")["reserved_microusd"] == 100


def test_window_stops_retrying_after_five_claimed_attempts(state):
    key = "REVIEW#i-example#300"
    for attempt in range(5):
        assert state.claim(key, str(attempt), 1000 + attempt)
        state.save(key, str(attempt), {"updated_at": 1000 + attempt}, release=True)
    assert not state.claim(key, "sixth", 2000)


def test_notice_transitions_suppress_unchanged_failures_but_keep_recovery_and_findings(state):
    runtime = Runtime.__new__(Runtime)
    runtime.state = state
    delivered = []
    runtime.notify = lambda report, *args: delivered.append(report)
    failed = {
        "review_status": "failed",
        "review_error": "Workspace budget exhausted",
        "summary": "Review unavailable",
        "findings": [],
        "coverage_gaps": ["Old export"],
    }

    def send(report, window):
        return runtime.deliver(report, f"REVIEW#i-test#{window}", "reviews/test", {})

    assert send(failed, 300) == "sent"
    assert send({**failed, "coverage_gaps": ["Old export", "No new traces"]}, 600) == "suppressed"
    assert send({**failed, "review_error": "Key revoked"}, 900) == "sent"
    idle = {**failed, "review_status": "idle", "review_error": "", "summary": "No recent evidence"}
    assert send(idle, 1200) == "sent"
    assert not delivered[-1]["notification_recovery"]
    assert send(idle, 1500) == "suppressed"
    healthy = {
        "review_status": "completed",
        "summary": "Reviewed",
        "findings": [],
        "coverage_gaps": [],
    }
    assert send(healthy, 1800) == "sent"
    assert delivered[-1]["notification_recovery"] is True
    assert send(healthy, 2100) == "suppressed"
    assert send(failed, 300) == "suppressed"  # Late failure cannot undo recovery.
    finding = {**healthy, "findings": [{"severity": "info"}]}
    assert send(finding, 2400) == "sent"
    assert send(finding, 2400) == "suppressed"  # Retry after recorded Slack acknowledgment.
    assert send(finding, 2700) == "sent"  # Never severity-filter real findings.


def test_notice_contention_and_failed_webhook_remain_retryable(state):
    runtime = Runtime.__new__(Runtime)
    runtime.state = state
    report = {
        "review_status": "failed",
        "summary": "Review unavailable",
        "review_error": "Budget exhausted",
        "findings": [],
        "coverage_gaps": [],
    }
    import time

    assert state.claim_notice("NOTICE#i-test", "other", int(time.time()))
    with pytest.raises(CoverageError, match="in progress"):
        runtime.deliver(report, "REVIEW#i-test#300", "reviews/test", {})
    state.save("NOTICE#i-test", "other", {"updated_at": 1}, release=True)

    def unavailable(*args):
        raise CoverageError("Webhook rejected delivery")

    runtime.notify = unavailable
    with pytest.raises(CoverageError, match="Webhook"):
        runtime.deliver(report, "REVIEW#i-test#300", "reviews/test", {})
    assert "fingerprint" not in state.get("NOTICE#i-test")
    assert "lease_owner" not in state.get("NOTICE#i-test")
    runtime.notify = lambda *args: None
    assert runtime.deliver(report, "REVIEW#i-test#300", "reviews/test", {}) == "sent"


def test_operational_keys_do_not_overwrite_incident_records(state):
    state.table.put_item(Item={"pk": "REVIEW#i-example#300", "sk": "STATE", "status": "closed"})
    assert state.claim("REVIEW#i-example#300", "worker", 1000)
    assert state.get("REVIEW#i-example#300")["status"] == "processing"
    assert (
        state.table.get_item(Key={"pk": "REVIEW#i-example#300", "sk": "STATE"})["Item"]["status"]
        == "closed"
    )


def test_price_tiers_and_per_call_cap_are_reserved_before_inference(state, monkeypatch):
    from review import MAX_OUTPUT_TOKENS, input_size

    runtime = Runtime.__new__(Runtime)
    runtime.state, runtime.http = state, None
    runtime.config = {"inference_budget_usd": 100}
    price = {
        "prompt": "0.000001",
        "completion": "0.000002",
        "overrides": [{"prompt": "0.000002", "completion": "0.000004"}],
    }
    monkeypatch.setattr(
        "worker.get_json",
        lambda *args, **kwargs: {"data": [{"id": "anthropic/claude-example", "pricing": price}]},
    )
    reserved = runtime.reserve_inference("anthropic/claude-example", {})
    assert reserved == input_size({}) * 2 + MAX_OUTPUT_TOKENS * 4
    price["request"] = "1"
    with pytest.raises(CoverageError, match="per-call"):
        runtime.reserve_inference("anthropic/claude-example", {})
    price["request"] = "NaN"
    with pytest.raises(CoverageError, match="invalid"):
        runtime.reserve_inference("anthropic/claude-example", {})
    assert state.get("BUDGET#inference")["reserved_microusd"] == reserved


def test_pending_index_keeps_operational_retries_separate(state):
    assert state.claim("REVIEW#i-one#300", "worker", 1000)
    assert state.claim("REVIEW#i-two#300", "worker", 1000)
    state.save("REVIEW#i-two#300", "worker", {"status": "done"}, release=True)
    state.table.put_item(
        Item={"pk": "REVIEW#historical", "sk": "STATE", "status": "processing", "updated_at": 1000}
    )
    assert [item["pk"] for item in state.pending()] == ["REVIEW#i-one#300"]
