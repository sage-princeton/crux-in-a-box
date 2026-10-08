"""Merge independent read models for display without cross-table mutations."""

import time


def coverage_rows(status_rows, incident_rows, now=None):
    now = int(time.time()) if now is None else now
    incidents = {row["instance_id"]: row for row in incident_rows}
    seen, rows = set(), []
    for item in status_rows:
        if "instance_id" not in item:
            continue
        row = dict(item)
        seen.add(row["instance_id"])
        latest = row.get("latest", {})
        threshold = row.get("stale_seconds", 1800)
        row["stale"] = bool(latest) and now - latest.get("evidence_at", 0) > threshold
        row["inventory_stale"] = now - row.get("inventory_at", 0) > threshold
        row["attention"] = (
            bool(latest.get("report", {}).get("alert"))
            or row["stale"]
            or row.get("attempt") == "failed"
        )
        row["review"] = incidents.get(row["instance_id"], {})
        row["status_registered"] = True
        rows.append(row)
    for iid, incident in incidents.items():
        if iid not in seen:
            rows.append(
                {
                    **incident,
                    "sk": "incident-" + iid,
                    "workload_id": "",
                    "status_registered": False,
                    "review": incident,
                    "attention": False,
                    "success_end": 0,
                }
            )
    return sorted(
        rows,
        key=lambda row: (
            row.get("state") != "running",
            not row["attention"],
            -row.get("success_end", 0),
            row.get("slug", "").casefold(),
            row["sk"],
        ),
    )
