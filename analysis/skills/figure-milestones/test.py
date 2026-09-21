#!/usr/bin/env python3
"""Test the milestones collector and builder.

Fixture: a PLAN.md in the harness template shape with three milestones —
one completed per its Status cell, one completed per the actuals JSON (which
must override a stale Status cell), one pending — and a fixture timeline
whose cumulative spend rises $1 per call every 6 minutes from launch. Real
data: the codex-astra export's PLAN.md at the last revision that still held
the original table (2e0d274).

Run from the repo root: python3 analysis/skills/figure-milestones/test.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "_lib"))
from figstyle import find_chrome, png_size  # noqa: E402

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


PLAN = """# PLAN.md

## Approach & milestones

| #   | Milestone                                                                | Deadline | Status |
| --- | ------------------------------------------------------------------------ | -------- | ------ |
| 1   | Environment verified + paper skeleton compiles from the venue template   | 2026-01-01 13:00 UTC | Completed 2026-01-01 12:40 UTC |
| 2   | Candidate approaches tested; direction chosen                            | 2026-01-01 15:00 UTC | Completed 2026-01-01 20:00 UTC (stale) |
| 3   | Main results in                                                          | 2026-01-01 18:30Z | Pending |

## Work in flight
"""


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


with tempfile.TemporaryDirectory(prefix="crux-fig-test-") as td:
    tdp = Path(td)
    (tdp / "PLAN.md").write_text(PLAN)
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    ev = [{"event": "run.context", "ts": iso(t0), "run_hours": 10.0, "api_budget_usd": 100.0}]
    for i in range(60):
        ev.append({"event": "model.usage", "ts": iso(t0 + timedelta(minutes=6 * i)), "cost_usd": 1.0,
                   "cum_cost_usd": float(i + 1)})
    (tdp / "t.jsonl").write_text("\n".join(json.dumps(e) for e in ev) + "\n")
    (tdp / "actuals.json").write_text(json.dumps({"milestones": [
        {"num": 2, "actual": "2026-01-01T16:12:00Z", "note": "LOG.md 16:12 — direction chosen"}]}))
    out = run([str(HERE / "collect.py"), "--plan", str(tdp / "PLAN.md"), "--timeline", str(tdp / "t.jsonl"),
               "--actuals", str(tdp / "actuals.json"), "--out", str(tdp / "m.json")])
    check(out.returncode == 0, f"collect (fixture) failed: {out.stderr[-300:]}")
    if out.returncode == 0:
        d = json.load(open(tdp / "m.json"))
        m = {x["num"]: x for x in d["milestones"]}
        check(d["launch"] == "2026-01-01T12:00:00Z", f"launch {d['launch']}")
        check(d["wall_limit_h"] == 10.0 and d["api_budget_usd"] == 100.0, "allotments not read")
        check(m[1]["planned_hours"] == 1.0 and m[1]["actual_hours"] == 0.7 and m[1]["actual_source"] == "status",
              f"M1 {m[1]['planned_hours']} {m[1]['actual_hours']} {m[1]['actual_source']}")
        check(m[2]["actual_hours"] == 4.2 and m[2]["actual_source"] == "actuals",
              f"M2 actuals JSON must override the Status cell: {m[2]['actual_hours']} {m[2]['actual_source']}")
        check(m[3]["planned_hours"] == 6.5 and m[3]["actual"] is None, f"M3 {m[3]['planned_hours']} {m[3]['actual']}")
        # spend: $1 per call at minutes 0,6,12,...; at 13:00 (60 min) 11 calls have landed
        check(m[1]["spend_at_planned"] == 11.0, f"M1 spend_at_planned {m[1]['spend_at_planned']} != 11.0")
        check(m[1]["spend_at_actual"] == 7.0, f"M1 spend_at_actual {m[1]['spend_at_actual']} != 7.0")
        check(m[2]["spend_at_actual"] == 43.0, f"M2 spend_at_actual {m[2]['spend_at_actual']} != 43.0")
        check("Environment verified" in m[1]["label"], f"label {m[1]['label']!r}")
        print(f"[figure-milestones] fixture: {len(d['milestones'])} milestones; "
              f"M1 {m[1]['planned_hours']}h→{m[1]['actual_hours']}h, M2 {m[2]['planned_hours']}h→{m[2]['actual_hours']}h")
    try:
        find_chrome()
        png = tdp / "fig.png"
        out = run([str(HERE / "build.py"), "--data", str(tdp / "m.json"), "--out", str(png), "--label", "Fixture · Codex CLI"])
        check(out.returncode == 0, f"build failed: {out.stderr[-400:]}")
        if out.returncode == 0:
            w, h = png_size(str(png))
            check(w == 812 * 2 and 500 < h < 2000, f"unexpected PNG size {w}x{h}")
            print(f"[figure-milestones] render: {w}x{h}px")
    except RuntimeError as e:
        print(f"[figure-milestones] SKIP render: {e}")

def find_run(name: str):
    """The run's repo: under runs-export/ in crux-in-a-box, or this repo itself when the skills ship inside an export."""
    for cand in (REPO / "runs-export" / name / "repo", REPO):
        if (cand / "host" / f"{name}.timeline.jsonl.gz").exists():
            return cand
    return None


astra = find_run("codex-astra")
if astra and (astra / "PLAN.md").exists():
    out = run([str(HERE / "collect.py"), "--plan", str(astra / "PLAN.md"), "--plan-rev", "2e0d274",
               "--timeline", str(astra / "host" / "codex-astra.timeline.jsonl.gz")])
    check(out.returncode == 0, f"collect (codex-astra) failed: {out.stderr[-300:]}")
    if out.returncode == 0:
        d = json.loads(out.stdout)
        m = {x["num"]: x for x in d["milestones"]}
        check(sorted(m) == list(range(1, 8)), f"astra milestones {sorted(m)}")
        check(d["launch"] == "2026-09-08T16:11:11Z", f"astra launch {d['launch']}")
        check(m[1]["planned_hours"] == 1.0, f"astra M1 planned {m[1]['planned_hours']}")
        check(m[7]["planned_hours"] == 168.0, f"astra M7 planned {m[7]['planned_hours']}")
        check(m[3]["spend_at_planned"] is not None and 0 < m[3]["spend_at_planned"] < 10000, "astra spend_at_planned")
        print(f"[figure-milestones] codex-astra@2e0d274: {len(d['milestones'])} milestones, "
              f"M1 planned {m[1]['planned_hours']}h, M7 planned {m[7]['planned_hours']}h")
    out = run([str(HERE / "collect.py"), "--plan", str(astra / "PLAN.md"), "--start", "2026-09-08T16:11:11Z"])
    check(out.returncode != 0, "astra's final PLAN.md has no table; the collector must refuse, not guess")
else:
    print("[figure-milestones] SKIP real data: codex-astra run data not found")

if failures:
    print("FAIL:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("[figure-milestones] PASS")
