import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from review import CoverageError
from worker import LEASE_SECONDS, State


@pytest.fixture
def state():
    with mock_aws():
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="monitoring", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}])
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
    state.save(key, "first", {"status": "pending_notification", "artifact_prefix": "reviews/saved"}, release=True)
    assert state.claim(key, "retry", 1001)
    item = state.get(key)
    assert item["status"] == "pending_notification"
    assert item["artifact_prefix"] == "reviews/saved"


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
