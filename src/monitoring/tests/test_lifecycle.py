from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from conftest import ROOT, ingest, report
from sqlalchemy import text

from lifecycle import Conflict, evidence_anchors, public_incident
from review import CoverageError
from store import Budget


def test_alembic_roundtrip(db):
    config = Config(str(ROOT / "alembic.ini"))
    command.downgrade(config, "base")
    with db.connect() as connection:
        assert connection.execute(text("SELECT to_regclass('workloads')")).scalar() is None
    command.upgrade(config, "head")
    with db.connect() as connection:
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0001"
        )
        undocumented = connection.execute(
            text("""
                SELECT t.relname, a.attname FROM pg_class t
                JOIN pg_namespace n ON n.oid=t.relnamespace
                JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum>0 AND NOT a.attisdropped
                WHERE n.nspname=current_schema() AND t.relkind='r'
                    AND (COALESCE(trim(obj_description(t.oid)), '')=''
                        OR COALESCE(trim(col_description(t.oid, a.attnum)), '')='')
            """)
        ).all()
    assert undocumented == [], f"Missing database comments: {undocumented}"


def test_workload_uuidv7_survives_renames_and_separates_replacements(store, target):
    original = target["key"]
    renamed = {**target, "name": "renamed", "slug": "renamed"}
    replacement = {**renamed, "instance_id": "i-replacement"}
    store.sync([renamed, replacement], 1800, 1800)
    assert UUID(original).version == UUID(replacement["key"]).version == 7
    assert renamed["key"] == original != replacement["key"]
    assert {row["key"]: row["name"] for row in store.fleet()} == {
        original: "renamed",
        replacement["key"]: "renamed",
    }


def test_incident_identity_and_closed_state_survive_repeated_observations(store, target):
    item = ingest(store, target)
    assert UUID(item["id"]).version == 7
    closed = store.transition(item["id"], item["version"], "closed", {"id": "operator"}, "Resolved")
    ingest(store, target, 1800)
    repeated = store.incident(item["id"])
    assert repeated["status"] == "closed" and repeated["review_count"] == 2
    assert len(store.instance_page(target["instance_id"], None, None, 50)) == 1
    with pytest.raises(Conflict):
        store.transition(item["id"], closed["version"], "open", {"id": "operator"}, "")
    reopened = store.transition(item["id"], repeated["version"], "open", {"id": "operator"}, "")
    ingest(store, target, 1800)
    assert store.incident(item["id"])["version"] == reopened["version"]
    events, _ = store.incident_history(item["id"], "events", None)
    observations, _ = store.incident_history(item["id"], "observations", None)
    assert [e["status"] for e in events] == ["closed", "open"]
    assert events[0]["note"] == "Resolved" and len(observations) == 2
    assert "Private evidence" not in str(public_incident(reopened))


def test_missing_or_fabricated_anchors_are_rejected(store, target):
    result = {"report": report("invented"), "artifact_prefix": "private"}
    with pytest.raises(ValueError, match="anchor"):
        store.ingest(target["key"], 900, result, {"real": "real"})
    del result["report"]["findings"][0]["anchor_id"]
    with pytest.raises(KeyError):
        store.ingest(target["key"], 900, result, {})
    assert store.instance_page(target["instance_id"], None, None, 50) == []


def test_workspace_append_preserves_existing_event_identity():
    source = {"id": "workspace/LOG.md", "kind": "workspace", "data": "event one\n"}
    before = evidence_anchors([source])
    source["data"] += "event two\n"
    after = evidence_anchors([source])
    assert set(before) < set(after)


def test_budget_reservations_are_atomic_and_separate(store):
    budget = Budget(store, "status")
    budget.reserve(600, 1000)
    with pytest.raises(CoverageError, match="exhausted"):
        budget.reserve(600, 1000)
    Budget(store, "incident").reserve(900, 1000)
    with store.db.connect() as connection:
        assert dict(
            connection.execute(text("SELECT scope,reserved_microusd FROM budgets")).all()
        ) == {"status": 600, "incident": 900}
