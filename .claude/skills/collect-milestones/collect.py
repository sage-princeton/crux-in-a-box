#!/usr/bin/env python3
"""Collect planned-vs-actual milestone data for a CRUX run (stdlib only).

Planned deadlines are parsed from the run workspace's PLAN.md milestone table
(the row table whose header contains "Deadline (absolute)"). Actual completion
times are judgment calls cross-validated against LOG.md, so they are provided
as an input JSON: {"launch": ISO, "milestones": [{"num", "label", "actual"}]}.

Outputs, per milestone: planned/actual as hours after launch (the *-time
dumbbell) and, when a cost CSV is given, cumulative API spend at each time
(the *-api dumbbell).

Usage:
  python3 collect.py --plan <PLAN.md> --actuals <actuals.json> \
    [--cost-csv <cost.csv>]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone


def parse_ts(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# Deadlines in PLAN.md look like "2026-07-02 16:00Z" (sometimes with trailing
# retarget notes); take the FIRST timestamp in the cell.
DEADLINE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})Z?")


def parse_plan(path: str) -> dict[int, datetime]:
    """Milestone number -> planned deadline, from the PLAN.md table."""
    lines = open(path, encoding="utf-8").read().splitlines()
    header_i = next(
        (i for i, l in enumerate(lines) if "Deadline (absolute)" in l), None
    )
    if header_i is None:
        raise SystemExit(f"{path}: no milestone table (header 'Deadline (absolute)')")
    planned: dict[int, datetime] = {}
    for line in lines[header_i + 2 :]:  # skip the |---| separator row
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        m = DEADLINE_RE.search(cells[2])
        if not m:
            continue
        planned[int(cells[0])] = parse_ts(f"{m.group(1)}T{m.group(2)}")
    if not planned:
        raise SystemExit(f"{path}: milestone table parsed to zero rows")
    return planned


def load_cost(path: str) -> list[tuple[datetime, float]]:
    with open(path) as f:
        return sorted(
            (parse_ts(r["time"]), float(r["cost"])) for r in csv.DictReader(f)
        )


def spend_at(rows: list[tuple[datetime, float]], t: datetime) -> float:
    return sum(c for ts, c in rows if ts <= t)


def hours_after(launch: datetime, t: datetime) -> float:
    # round half-up to 1 decimal (python's round() is banker's)
    h = (t - launch).total_seconds() / 3600
    return int(h * 10 + 0.5) / 10


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--plan", required=True, help="run workspace PLAN.md")
    p.add_argument("--actuals", required=True, help="actuals JSON (see docstring)")
    p.add_argument("--cost-csv", help="per-minute Anthropic cost CSV")
    args = p.parse_args()

    planned = parse_plan(args.plan)
    actuals = json.loads(open(args.actuals).read())
    launch = parse_ts(actuals["launch"])
    cost = load_cost(args.cost_csv) if args.cost_csv else None

    out = []
    for m in actuals["milestones"]:
        num = m["num"]
        if num not in planned:
            raise SystemExit(f"milestone {num} not found in {args.plan}")
        actual = parse_ts(m["actual"])
        row = {
            "num": num,
            "label": m.get("label", ""),
            "planned": planned[num].isoformat(),
            "actual": actual.isoformat(),
            "planned_hours": hours_after(launch, planned[num]),
            "actual_hours": hours_after(launch, actual),
        }
        if cost:
            row["spend_at_planned"] = round(spend_at(cost, planned[num]))
            row["spend_at_actual"] = round(spend_at(cost, actual))
        out.append(row)
    print(json.dumps({"launch": launch.isoformat(), "milestones": out}, indent=2))


if __name__ == "__main__":
    main()
