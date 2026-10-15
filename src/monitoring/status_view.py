"""Derive status display fields from the shared workload inventory."""

import time


def coverage_rows(rows):
    now = int(time.time())
    result = []
    for item in rows:
        row = dict(item)
        latest = row.get("latest", {})
        threshold = row["stale_seconds"]
        row["stale"] = bool(latest) and now - latest.get("evidence_at", 0) > threshold
        row["inventory_stale"] = now - row["inventory_at"] > threshold
        row["attention"] = (
            bool(latest.get("report", {}).get("alert"))
            or row["stale"]
            or row["attempt"] == "failed"
        )
        row["review"] = item
        result.append(row)
    return sorted(
        result,
        key=lambda row: (
            row["state"] != "running",
            not row["attention"],
            -row["success_end"],
            row["slug"].casefold(),
            row["key"],
        ),
    )
