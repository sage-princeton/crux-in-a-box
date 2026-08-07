#!/usr/bin/env python3
"""Collect the resource-consumption series for a CRUX run (stdlib only).

Inputs: a per-minute Anthropic cost CSV (time,tokens,cost), an hourly RunPod
CSV (time_utc,gpuId,amount_usd,...,in_run_window), the run's budgets, and the
original deadline + actual end. Output: JSON shaped like a LinesData entry in
app/data/plots.ts (wall-clock / API-spend / GPU series sampled every 6h, with
extra samples bracketing the deadline discontinuity), plus endpoint totals.

Usage:
  python3 collect.py --cost-csv C.csv --runpod-csv R.csv \
    --api-budget 3000 --gpu-cap 100 \
    --deadline 2026-07-07T00:30Z --end 2026-07-08T00:04Z \
    [--sections sections.json]
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone


def parse_ts(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_cost(path: str) -> list[tuple[datetime, float]]:
    with open(path) as f:
        rows = [(parse_ts(r["time"]), float(r["cost"])) for r in csv.DictReader(f)]
    return sorted(rows)


def load_runpod(path: str) -> list[tuple[datetime, float]]:
    """Hourly in-run-window RunPod spend, summed across GPUs per bucket."""
    buckets: dict[datetime, float] = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            if r["in_run_window"] != "1":
                continue
            t = parse_ts(r["time_utc"])
            buckets[t] = buckets.get(t, 0.0) + float(r["amount_usd"])
    return sorted(buckets.items())


def cumulative_at(rows: list[tuple[datetime, float]], t: datetime) -> float:
    return sum(v for ts, v in rows if ts <= t)


def build(args: argparse.Namespace) -> dict:
    cost = load_cost(args.cost_csv)
    runpod = load_runpod(args.runpod_csv) if args.runpod_csv else []
    start = cost[0][0]
    deadline = parse_ts(args.deadline)
    end = parse_ts(args.end)

    def hours(t: datetime) -> float:
        return (t - start).total_seconds() / 3600

    # 6-hourly grid from start, plus the deadline discontinuity and the end.
    samples = set()
    t = start
    while t <= end:
        samples.add(t)
        t += timedelta(hours=6)
    samples |= {deadline, deadline + timedelta(minutes=6), end}

    time_pts, spend_pts, gpu_pts = [], [], []
    for t in sorted(samples):
        h = round(hours(t), 1)
        allowance = hours(deadline) if t <= deadline else hours(end)
        time_pts.append([h, round(min(hours(t) / allowance * 100, 100), 1)])
        spend_pts.append([h, round(cumulative_at(cost, t) / args.api_budget * 100, 1)])
        gpu_pts.append([h, round(cumulative_at(runpod, t) / args.gpu_cap * 100, 1)])

    out = {
        "start": start.isoformat(),
        "original_deadline_hour": round(hours(deadline), 1),
        "end_hour": round(hours(end), 1),
        "total_cost": round(cumulative_at(cost, end), 2),
        "total_runpod_cost": round(cumulative_at(runpod, end), 2),
        "series": [
            {"name": "Wall-clock time", "points": time_pts},
            {"name": "Anthropic API spend", "points": spend_pts},
            {"name": "GPU compute", "points": gpu_pts},
        ],
    }

    # Optional run-section boundaries: validate they tile start -> end and
    # emit them in hours-after-start for the chart's annotations/markers.
    if args.sections:
        sections = json.loads(open(args.sections).read())["sections"]
        for i, s in enumerate(sections):
            if i and s["start"] != sections[i - 1]["end"]:
                raise SystemExit(
                    f"sections do not tile: {sections[i-1]['end']} != {s['start']}"
                )
        out["sections"] = [
            {"label": s["title"], "startHour": round(hours(parse_ts(s["start"])), 1)}
            for s in sections
        ]
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cost-csv", required=True)
    p.add_argument("--runpod-csv")
    p.add_argument("--api-budget", type=float, required=True)
    p.add_argument("--gpu-cap", type=float, required=True)
    p.add_argument("--deadline", required=True, help="original deadline, ISO UTC")
    p.add_argument("--end", required=True, help="actual run end, ISO UTC")
    p.add_argument("--sections", help="optional *_run_sections.json to validate/emit")
    print(json.dumps(build(p.parse_args()), indent=2))


if __name__ == "__main__":
    main()
