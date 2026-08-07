#!/usr/bin/env python3
"""Test collect.py against both CRUX-2 run workspaces.

Planned deadlines are parsed live from each repo's PLAN.md; actuals use the
LOG.md-validated timestamps from visualizations/milestone_spend.py. Ground
truth: the crux2-*-milestones-{time,api} dumbbells in app/data/plots.ts.
Run from the repo root: python3 .claude/skills/collect-milestones/test.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "visualizations" / "crux-2-data"
COLLECT = Path(__file__).parent / "collect.py"

RUNS: Dict[str, Any] = {
    "personas": {
        "plan": REPO / "crux-trait-space" / "PLAN.md",
        "cost": DATA / "anthropic_apikey_01B1hgvw7pqUyZjp8K4jJVpG_1m_20260702T000000Z_20260710T174700Z_cost.csv",
        "launch": "2026-07-02T01:39Z",
        # LOG.md-validated actuals (see visualizations/milestone_spend.py)
        "actuals": {
            1: "2026-07-02T01:42Z", 2: "2026-07-02T06:53Z", 3: "2026-07-05T13:15Z",
            4: "2026-07-06T23:52Z", 5: "2026-07-06T23:44Z", 6: "2026-07-07T00:08Z",
        },
        # expected (planned_hours, actual_hours, spend_planned, spend_actual)
        # from app/data/plots.ts crux2-personas-milestones-{time,api}
        "expected": {
            1: (4.4, 0.1, 62, 1),
            2: (42.4, 5.2, 345, 69),
            3: (70.4, 83.6, 380, 443),
            4: (106.4, 118.2, 655, 810),
            5: (116.4, 118.1, 757, 805),
            6: (120.0, 118.5, 867, 822),
        },
    },
    "tabpfn": {
        "plan": REPO / "pfn-harmful-shift-detector" / "PLAN.md",
        "cost": DATA / "anthropic_apikey_01MJ2koPsnrC5hdVU8qpjZYd_1m_20260702T000000Z_20260710T174700Z_cost.csv",
        "launch": "2026-07-02T01:33Z",
        "actuals": {
            1: "2026-07-02T01:42Z", 2: "2026-07-03T14:04Z", 3: "2026-07-03T14:15Z",
            4: "2026-07-06T22:05Z", 5: "2026-07-06T22:05Z", 6: "2026-07-06T22:35Z",
        },
        "expected": {
            1: (14.5, 0.2, 192, 3),
            2: (44.5, 36.5, 331, 298),
            3: (76.5, 36.7, 635, 302),
            4: (102.5, 116.5, 777, 883),
            5: (112.5, 116.5, 815, 883),
            6: (119.5, 117.0, 911, 891),
        },
    },
}

failures = []
for name, cfg in RUNS.items():
    actuals = {
        "launch": cfg["launch"],
        "milestones": [{"num": n, "actual": t} for n, t in cfg["actuals"].items()],
    }
    actuals_path = Path(tempfile.gettempdir()) / f"crux-{name}-actuals.json"
    actuals_path.write_text(json.dumps(actuals))
    out = subprocess.run(
        [sys.executable, str(COLLECT), "--plan", str(cfg["plan"]),
         "--actuals", str(actuals_path), "--cost-csv", str(cfg["cost"])],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        failures.append(f"{name}: collect.py failed: {out.stderr.strip()[:200]}")
        continue
    got = {m["num"]: m for m in json.loads(out.stdout)["milestones"]}
    for num, (ph, ah, sp, sa) in cfg["expected"].items():
        m = got.get(num)
        if not m:
            failures.append(f"{name} M{num}: missing")
            continue
        if abs(m["planned_hours"] - ph) > 0.06:
            failures.append(f"{name} M{num}: planned_hours {m['planned_hours']} != {ph}")
        if abs(m["actual_hours"] - ah) > 0.06:
            failures.append(f"{name} M{num}: actual_hours {m['actual_hours']} != {ah}")
        if abs(m["spend_at_planned"] - sp) > 1:
            failures.append(f"{name} M{num}: spend_at_planned {m['spend_at_planned']} != {sp}")
        if abs(m["spend_at_actual"] - sa) > 1:
            failures.append(f"{name} M{num}: spend_at_actual {m['spend_at_actual']} != {sa}")
    print(f"[collect-milestones] {name}: {len(got)} milestones checked")

if failures:
    print("FAIL:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("[collect-milestones] PASS")
