import json
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
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"
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


def test_slack_cursor_migration_skips_existing_incidents_without_losing_history(store, target):
    original = ingest(store, target)
    empty = {**target, "instance_id": "i-empty", "slug": "empty"}
    store.sync([target, empty], 900, 1800)
    config = Config(str(ROOT / "alembic.ini"))
    command.downgrade(config, "0001")
    command.upgrade(config, "head")
    assert store.new_incidents() == []
    assert store.incident(original["id"])["status"] == "open"
    with store.db.connect() as connection:
        cursors = dict(
            connection.execute(text("SELECT key, slack_incident_cursor FROM workloads")).all()
        )
        nullable = connection.execute(
            text("""
            SELECT is_nullable FROM information_schema.columns
            WHERE table_name='workloads' AND column_name='slack_incident_cursor'
        """)
        ).scalar_one()
    assert cursors[UUID(target["key"])] == UUID(original["id"])
    assert cursors[UUID(empty["key"])] == UUID(int=0)
    assert nullable == "NO"
    new = ingest(store, target, 450, anchor="observation:late")
    assert store.new_incidents() == [
        {"key": target["key"], "slug": target["slug"], "new_incidents": 1, "through": new["id"]}
    ]
    fresh = {**target, "instance_id": "i-new", "slug": "new"}
    store.sync([target, empty, fresh], 1800, 1800)
    with store.db.connect() as connection:
        assert connection.execute(
            text("SELECT slack_incident_cursor FROM workloads WHERE key=:key"),
            {"key": fresh["key"]},
        ).scalar_one() == UUID(int=0)


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
    assert public_incident(reopened)["what_happened"] == "Private evidence"
    assert (
        not {"anchor", "detector", "workload_key", "version", "latest_finding"}
        & public_incident(reopened).keys()
    )


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


def test_archived_finding_enriches_existing_incident_without_reopening_or_alerting(store, target):
    item = ingest(store, target)
    closed = store.transition(item["id"], item["version"], "closed", {"id": "operator"}, "Resolved")
    store.acknowledge_incidents(target["key"], item["id"])
    finding = {
        "category": "upload failed",
        "evidence": "Uploading /root/usr/abc/123 failed with EACCES; access did not succeed.",
        "benign_explanation": "The path may have been entered by mistake.",
        "severity": "low",
        "confidence": "medium",
    }
    # Archived incidents saved useful narrative only in their observations.
    with store.db.begin() as connection:
        connection.execute(
            text(
                "UPDATE observations SET detail=jsonb_set(detail, '{finding}', CAST(:finding AS jsonb)) WHERE incident_id=:id"
            ),
            {"id": item["id"], "finding": json.dumps(finding)},
        )
    enriched = store.incident(item["id"])
    assert enriched["title"] == "Unexpected upload destination: upload failed"
    assert enriched["what_happened"] == finding["evidence"]
    assert "did not establish the cause" in enriched["why_it_happened"]
    assert "may have been entered by mistake" in enriched["why_it_happened"]
    assert (enriched["id"], enriched["status"], enriched["version"]) == (
        closed["id"],
        "closed",
        closed["version"],
    )
    assert (
        store.instance_page(target["instance_id"], None, None, 50)[0]["title"] == enriched["title"]
    )
    assert store.new_incidents() == []


def test_finding_explanations_and_latest_window_are_persisted(store, target):
    result = {"report": report(), "artifact_prefix": "private"}
    finding = result["report"]["findings"][0]
    finding.update(
        detector_id="other",
        category="file permissions",
        what_happened="Reading /root/usr/abc/123 failed with EACCES.",
        why_it_happened="The process lacks permission; the path is outside the supplied task.",
        next_steps="Confirm the requested file and task permissions with the operator.",
    )
    store.ingest(target["key"], 1800, result, {"observation:123": "observation:123"})
    finding["what_happened"] = "Older observation"
    store.ingest(target["key"], 900, result, {"observation:123": "observation:123"})
    item = store.instance_page(target["instance_id"], None, None, 50)[0]
    assert item["title"] == "Activity requires review: file permissions"
    assert item["what_happened"] == "Reading /root/usr/abc/123 failed with EACCES."
    assert item["why_it_happened"] == finding["why_it_happened"]
    assert item["next_steps"] == finding["next_steps"]
    with store.db.connect() as connection:
        saved = connection.execute(
            text("SELECT detail FROM incidents WHERE id=:id"), {"id": item["id"]}
        ).scalar_one()
    assert saved["what_happened"] == item["what_happened"]


@pytest.mark.parametrize("status", ["failed", "completed"])
def test_monitoring_health_explains_failure_and_coverage_gaps(store, target, status):
    reason = "Incident assessment unavailable (Subject model family is unknown; configure a trusted family mapping)."
    gap = "Langfuse returned no observations for this collection window."
    result = {
        "artifact_prefix": "private",
        "report": {
            "review_status": status,
            "summary": "Review complete",
            "findings": [],
            "review_error": reason,
            "coverage_gaps": [gap, reason],
        },
    }
    store.ingest(target["key"], 900, result, {})
    item = store.instance_page(target["instance_id"], None, None, 50)[0]
    assert reason in item["why_it_happened"] and gap in item["why_it_happened"]
    if status == "failed":
        assert item["title"] == "AI reviews unavailable: unknown subject model"
        assert "no safety verdict" in item["what_happened"]
        assert "trusted subject-family mapping" in item["next_steps"]
    else:
        assert "incomplete evidence" in item["what_happened"]
        assert "coverage gaps" in item["next_steps"]


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
