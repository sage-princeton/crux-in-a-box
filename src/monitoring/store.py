"""Monitoring state and operator actions in PostgreSQL."""

import json
import time

from sqlalchemy import text

from database import engine
from lifecycle import DETECTORS, Conflict
from review import CoverageError


def encoded(value):
    return json.dumps(value)


class Store:
    def __init__(self, db=None):
        self.db = db or engine()

    def health(self):
        with self.db.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    def sync(self, inventory, now, stale_seconds):
        with self.db.begin() as connection:
            connection.execute(text("UPDATE workloads SET state='no longer enrolled'"))
            for item in inventory:
                key = connection.execute(
                    text("""
                    INSERT INTO workloads (instance_id, name, slug, state, inventory_at, stale_seconds)
                    VALUES (:instance_id, :name, :slug,
                        :state, :now, :stale)
                    ON CONFLICT (instance_id) DO UPDATE SET name=EXCLUDED.name, slug=EXCLUDED.slug,
                        state=EXCLUDED.state, inventory_at=EXCLUDED.inventory_at,
                        stale_seconds=EXCLUDED.stale_seconds
                    RETURNING key
                """),
                    {**item, "now": now, "stale": stale_seconds},
                ).scalar_one()
                item["key"] = str(key)

    def fleet(self):
        with self.db.connect() as connection:
            result = connection.execute(
                text("""
                SELECT w.*,
                    last_status.result AS status_attempt, latest.result AS latest,
                    last_incident.result AS incident_attempt, previous_incident.result AS incident_report,
                    (SELECT count(*) FROM assessments a WHERE a.workload_key=w.key
                        AND a.kind='incident') AS review_count
                FROM workloads w
                LEFT JOIN LATERAL (SELECT result FROM assessments a
                    WHERE a.workload_key=w.key AND kind='status'
                    ORDER BY window_end DESC LIMIT 1) last_status ON true
                LEFT JOIN LATERAL (SELECT result FROM assessments a
                    WHERE a.workload_key=w.key AND kind='status'
                        AND result->>'outcome'='completed'
                    ORDER BY window_end DESC LIMIT 1) latest ON true
                LEFT JOIN LATERAL (SELECT result FROM assessments a
                    WHERE a.workload_key=w.key AND kind='incident'
                    ORDER BY window_end DESC LIMIT 1) last_incident ON true
                LEFT JOIN LATERAL (SELECT result FROM assessments a
                    WHERE a.workload_key=w.key AND kind='incident'
                        AND result->>'outcome'='completed'
                    ORDER BY window_end DESC LIMIT 1) previous_incident ON true
                ORDER BY w.slug, w.key
            """)
            )
            rows = [dict(row) for row in result.mappings()]
        for row in rows:
            row["key"] = str(row["key"])
            attempt, incident = row.pop("status_attempt") or {}, row.pop("incident_attempt") or {}
            row["latest"] = row["latest"] or {}
            row["incident_report"] = (row["incident_report"] or {}).get("report", {})
            row.update(
                attempt=attempt.get("outcome", "pending"),
                attempt_at=attempt.get("checked_at", 0),
                error=attempt.get("error", ""),
                success_end=row["latest"].get("window_end", 0),
                review_status=incident.get("outcome", "pending"),
                last_review=incident.get("window_end", 0),
            )
        return rows

    def collection(self, key, end):
        with self.db.connect() as connection:
            return connection.execute(
                text(
                    "SELECT evidence FROM collections WHERE workload_key=:key AND window_end=:end"
                ),
                {"key": key, "end": end},
            ).scalar()

    def save_collection(self, key, end, evidence):
        with self.db.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO collections VALUES (:key, :end, :now, CAST(:evidence AS jsonb))
                ON CONFLICT (workload_key, window_end) DO UPDATE SET evidence=EXCLUDED.evidence
            """),
                {"key": key, "end": end, "now": int(time.time()), "evidence": encoded(evidence)},
            )

    def assessment(self, key, end, kind):
        with self.db.connect() as connection:
            return connection.execute(
                text(
                    "SELECT result FROM assessments WHERE "
                    "workload_key=:key AND window_end=:end AND kind=:kind"
                ),
                {"key": key, "end": end, "kind": kind},
            ).scalar()

    def publish(self, key, end, kind, result):
        with self.db.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO assessments (workload_key, window_end, kind, result)
                VALUES (:key, :end, :kind, CAST(:result AS jsonb))
                ON CONFLICT DO NOTHING
            """),
                {"key": key, "end": end, "kind": kind, "result": encoded(result)},
            )

    def history(self, key, before):
        with self.db.connect() as connection:
            rows = (
                connection.execute(
                    text("""
                SELECT window_end, result FROM assessments WHERE workload_key=:key
                    AND kind='status' AND window_end < :before
                ORDER BY window_end DESC LIMIT 21
            """),
                    {"key": key, "before": int(before) if before else 2**63 - 1},
                )
                .mappings()
                .all()
            )
        return [row["result"] for row in rows[:20]], str(rows[19]["window_end"]) if len(
            rows
        ) > 20 else None

    def pending(self, now):
        with self.db.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    text("""
                SELECT c.window_end, c.evidence FROM collections c WHERE c.window_end>:before
                AND ((SELECT count(*) FROM assessments a WHERE a.workload_key=c.workload_key
                    AND a.window_end=c.window_end)<2 OR EXISTS (SELECT 1 FROM assessments a
                    WHERE a.workload_key=c.workload_key AND a.window_end=c.window_end
                        AND kind='incident' AND NOT ingested))
            """),
                    {"before": now - 86400},
                ).mappings()
            ]

    def ingest(self, key, end, result, anchors):
        report, prefix = result["report"], result["artifact_prefix"]
        status = report["review_status"]
        if status not in ("completed", "failed"):
            raise ValueError("Invalid review status")
        findings = []
        for finding in report["findings"]:
            detector, anchor = finding["detector_id"], finding["anchor_id"]
            if detector not in DETECTORS or detector.startswith("monitoring:"):
                raise ValueError("Unknown finding detector")
            if anchor not in anchors or anchors[anchor] not in finding["source_ids"]:
                raise ValueError("Finding anchor is not in its cited evidence")
            findings.append((detector, anchor, finding))
        health = (
            "unavailable"
            if status == "failed"
            else ("coverage" if report["coverage_gaps"] else None)
        )
        if health:
            findings.append(
                (
                    "monitoring:" + health,
                    "workload",
                    {
                        "severity": "high" if status == "failed" else "info",
                        "confidence": "high",
                        "evidence": report.get("review_error", report["summary"]),
                        "source_ids": [],
                        "coverage_gaps": report["coverage_gaps"],
                    },
                )
            )
        with self.db.begin() as connection:
            for detector, anchor, finding in findings:
                title, summary = DETECTORS[detector]
                detail = {
                    "title": title,
                    "summary": summary,
                    "kind": "monitoring" if detector.startswith("monitoring:") else "finding",
                    "severity": finding["severity"],
                    "confidence": finding["confidence"],
                }
                identity = connection.execute(
                    text("""
                    INSERT INTO incidents (workload_key, detector, anchor, first_seen,
                        last_seen, updated_at, detail)
                    VALUES (:key, :detector, :anchor, :end, :end, :now, CAST(:detail AS jsonb))
                    ON CONFLICT (workload_key, detector, anchor)
                    DO UPDATE SET anchor=EXCLUDED.anchor RETURNING id
                """),
                    {
                        "key": key,
                        "detector": detector,
                        "anchor": anchor,
                        "end": end,
                        "now": int(time.time()),
                        "detail": encoded(detail),
                    },
                ).scalar_one()
                observation = {
                    "at": end,
                    "review_id": f"{key}/{end}",
                    "finding": finding,
                    "artifact_prefix": prefix,
                    "provenance": result.get("model", {}),
                }
                added = connection.execute(
                    text("""
                    INSERT INTO observations VALUES (:id, :end, CAST(:detail AS jsonb))
                    ON CONFLICT DO NOTHING RETURNING incident_id
                """),
                    {"id": identity, "end": end, "detail": encoded(observation)},
                ).first()
                if added:
                    connection.execute(
                        text("""
                        UPDATE incidents SET first_seen=LEAST(first_seen,:end),
                            detail=CASE WHEN last_seen<=:end THEN CAST(:detail AS jsonb) ELSE detail END,
                            last_seen=GREATEST(last_seen,:end), updated_at=:now, version=version+1
                        WHERE id=:id
                    """),
                        {
                            "id": identity,
                            "end": end,
                            "now": int(time.time()),
                            "detail": encoded(detail),
                        },
                    )
            connection.execute(
                text(
                    "UPDATE assessments SET ingested=true WHERE "
                    "workload_key=:key AND window_end=:end AND kind='incident'"
                ),
                {"key": key, "end": end},
            )

    def _incident(self, row):
        row = dict(row)
        row["id"] = str(row["id"])
        row["workload_key"] = str(row["workload_key"])
        row.update(row.pop("detail"))
        row["observation_count"] = row["review_count"] = row.pop("observations", 0)
        return row

    def incident(self, identity):
        with self.db.connect() as connection:
            row = (
                connection.execute(
                    text("""
                SELECT i.*, w.slug AS workload_label, w.instance_id,
                    (SELECT count(*) FROM observations o WHERE o.incident_id=i.id) AS observations
                FROM incidents i JOIN workloads w ON w.key=i.workload_key WHERE i.id=:id
            """),
                    {"id": identity},
                )
                .mappings()
                .first()
            )
        return self._incident(row) if row else None

    def instance_page(self, instance, status, cursor, limit):
        with self.db.connect() as connection:
            rows = connection.execute(
                text("""
                SELECT i.*, w.slug AS workload_label, w.instance_id,
                    (SELECT count(*) FROM observations o WHERE o.incident_id=i.id) AS observations
                FROM incidents i JOIN workloads w ON w.key=i.workload_key
                WHERE w.instance_id=:instance AND (CAST(:status AS text) IS NULL OR i.status=:status)
                    AND (CAST(:cursor AS uuid) IS NULL OR i.id>CAST(:cursor AS uuid))
                ORDER BY i.id LIMIT :limit
            """),
                {"instance": instance, "status": status, "cursor": cursor, "limit": limit},
            ).mappings()
            return [self._incident(row) for row in rows]

    def incident_history(self, identity, kind, cursor):
        column, query = (
            (
                "version",
                "SELECT version, detail FROM incident_events WHERE incident_id=:id "
                "AND version>:cursor ORDER BY version LIMIT 51",
            )
            if kind == "events"
            else (
                "window_end",
                "SELECT window_end, detail FROM observations WHERE incident_id=:id "
                "AND window_end>:cursor ORDER BY window_end LIMIT 51",
            )
        )
        with self.db.connect() as connection:
            rows = (
                connection.execute(
                    text(query),
                    {"id": identity, "cursor": int(cursor or 0)},
                )
                .mappings()
                .all()
            )
        return [row["detail"] for row in rows[:50]], str(rows[49][column]) if len(
            rows
        ) > 50 else None

    def transition(self, identity, version, status, actor, note):
        if status not in ("open", "closed") or not actor or len(note) > 4000:
            raise ValueError("Invalid incident transition")
        now = int(time.time())
        with self.db.begin() as connection:
            row = (
                connection.execute(
                    text("SELECT status, version FROM incidents WHERE id=:id FOR UPDATE"),
                    {"id": identity},
                )
                .mappings()
                .first()
            )
            if not row:
                raise KeyError(identity)
            if row["version"] != int(version) or row["status"] == status:
                raise Conflict("Incident changed; reload before trying again.")
            connection.execute(
                text(
                    "UPDATE incidents SET status=:status, version=version+1, "
                    "updated_at=:now WHERE id=:id"
                ),
                {"id": identity, "status": status, "now": now},
            )
            event = {
                "at": now,
                "from_status": row["status"],
                "status": status,
                "actor": actor,
                "note": note,
                "version": int(version) + 1,
            }
            connection.execute(
                text("INSERT INTO incident_events VALUES (:id, :version, CAST(:event AS jsonb))"),
                {"id": identity, "version": event["version"], "event": encoded(event)},
            )
        return self.incident(identity)

    def notice(self, key):
        with self.db.connect() as connection:
            return connection.execute(
                text("SELECT fingerprint FROM notices WHERE key=:key"), {"key": key}
            ).scalar()

    def acknowledge(self, key, fingerprint):
        with self.db.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO notices VALUES (:key, :fingerprint, :now) ON CONFLICT (key)
                DO UPDATE SET fingerprint=EXCLUDED.fingerprint, delivered_at=EXCLUDED.delivered_at
            """),
                {"key": key, "fingerprint": fingerprint, "now": int(time.time())},
            )

    def summaries(self):
        with self.db.connect() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    text("""
                SELECT w.key, w.instance_id, w.slug,
                    (SELECT count(*) FROM incidents i WHERE i.workload_key=w.key
                        AND i.status='open') AS open_incidents,
                    (SELECT count(*) FROM incidents i WHERE i.workload_key=w.key) AS total_incidents,
                    (SELECT count(*) FROM assessments a WHERE a.workload_key=w.key
                        AND kind='incident') AS reviews,
                    (SELECT max(window_end) FROM assessments a WHERE a.workload_key=w.key
                        AND kind='incident') AS last_review
                FROM workloads w WHERE w.state='running' ORDER BY w.slug, w.key
            """)
                ).mappings()
            ]
        for row in rows:
            row["key"] = str(row["key"])
        return rows

    def login(self, token, request_id, expires_at):
        with self.db.begin() as connection:
            connection.execute(
                text("INSERT INTO login_requests VALUES (:token, :request, :expires)"),
                {"token": token, "request": request_id, "expires": expires_at},
            )

    def pending_login(self, token):
        with self.db.connect() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM login_requests WHERE token=:token"), {"token": token}
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def session(self, token):
        with self.db.connect() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM sessions WHERE token=:token"), {"token": token}
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def finish_login(self, login, token, actor, csrf, expires, now):
        with self.db.begin() as connection:
            used = connection.execute(
                text(
                    "DELETE FROM login_requests WHERE token=:token "
                    "AND expires_at>:now RETURNING token"
                ),
                {"token": login, "now": now},
            ).first()
            if not used:
                raise Conflict("Sign-in already used or expired.")
            connection.execute(
                text(
                    "INSERT INTO sessions VALUES (:token, CAST(:actor AS jsonb), :csrf, :expires)"
                ),
                {"token": token, "actor": encoded(actor), "csrf": csrf, "expires": expires},
            )

    def logout(self, token):
        with self.db.begin() as connection:
            connection.execute(text("DELETE FROM sessions WHERE token=:token"), {"token": token})

    def prune(self, before):
        with self.db.begin() as connection:
            connection.execute(
                text("DELETE FROM collections WHERE window_end<:before"), {"before": before}
            )
            now = {"now": int(time.time())}
            connection.execute(text("DELETE FROM sessions WHERE expires_at<:now"), now)
            connection.execute(text("DELETE FROM login_requests WHERE expires_at<:now"), now)


class Budget:
    def __init__(self, store, scope):
        self.store, self.scope = store, scope

    def reserve(self, amount, limit):
        if not 0 < amount <= limit <= 500_000_000:
            raise CoverageError("Invalid or insufficient inference budget")
        with self.store.db.begin() as connection:
            connection.execute(
                text("INSERT INTO budgets VALUES (:scope, 0) ON CONFLICT DO NOTHING"),
                {"scope": self.scope},
            )
            row = connection.execute(
                text(
                    "UPDATE budgets SET reserved_microusd=reserved_microusd+:amount "
                    "WHERE scope=:scope AND reserved_microusd<=:remaining RETURNING scope"
                ),
                {"scope": self.scope, "amount": amount, "remaining": limit - amount},
            ).first()
            if not row:
                raise CoverageError("Inference budget exhausted")
