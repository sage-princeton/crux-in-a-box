#!/usr/bin/env python3
"""Test collect.py against the CRUX-2 runs' checked-in data.

Ground truth: endpoint totals and spot-check points from the published
crux2-{tabpfn,personas}-resources charts in app/data/plots.ts.
Run from the repo root: python3 .claude/skills/collect-resource-curves/test.py
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "visualizations" / "crux-2-data"
COLLECT = Path(__file__).parent / "collect.py"

RUNS: Dict[str, Any] = {
    "tabpfn": {
        "cost": "anthropic_apikey_01MJ2koPsnrC5hdVU8qpjZYd_1m_20260702T000000Z_20260710T174700Z_cost.csv",
        "runpod": "tabpfn_runpod_hourly_by_gpu.csv",
        "gpu_cap": 100,
        # from plots.ts crux2-tabpfn-resources + its endLabels
        "total_cost": 1235,
        "total_runpod": 69,
        "api_spots": {48: 15.7, 114: 27.7, 142.5: 41.2},
        "gpu_spots": {60: 28.0, 142.5: 69.2},
        "n_sections": 5,
    },
    "personas": {
        "cost": "anthropic_apikey_01B1hgvw7pqUyZjp8K4jJVpG_1m_20260702T000000Z_20260710T174700Z_cost.csv",
        "runpod": "personas_runpod_hourly_by_gpu.csv",
        "gpu_cap": 500,
        "total_cost": 1130,
        "total_runpod": 392,
        "api_spots": {84: 15.2, 114: 24.0},
        "gpu_spots": {108: 58.9},
        "n_sections": 5,
    },
}

failures = []
for name, cfg in RUNS.items():
    out = subprocess.run(
        [
            sys.executable, str(COLLECT),
            "--cost-csv", str(DATA / cfg["cost"]),
            "--runpod-csv", str(DATA / cfg["runpod"]),
            "--api-budget", "3000", "--gpu-cap", str(cfg["gpu_cap"]),
            "--deadline", "2026-07-07T00:30Z", "--end", "2026-07-08T00:04Z",
            "--sections", str(DATA / f"{name}_run_sections.json"),
        ],
        capture_output=True, text=True, check=True,
    )
    d = json.loads(out.stdout)
    pts = {s["name"]: dict((p[0], p[1]) for p in s["points"]) for s in d["series"]}

    def check(label, got, want, tol):
        if abs(got - want) > tol:
            failures.append(f"{name}: {label} got {got}, expected {want}")

    check("total_cost", d["total_cost"], cfg["total_cost"], 1)
    check("total_runpod", d["total_runpod_cost"], cfg["total_runpod"], 1)
    for h, v in cfg["api_spots"].items():
        check(f"api@{h}h", pts["Anthropic API spend"].get(h, -1), v, 0.05)
    for h, v in cfg["gpu_spots"].items():
        check(f"gpu@{h}h", pts["GPU compute"].get(h, -1), v, 0.05)
    if len(d["sections"]) != cfg["n_sections"]:
        failures.append(f"{name}: {len(d['sections'])} sections, expected {cfg['n_sections']}")
    # wall-clock must hit 100 at the deadline and again at the end (there are
    # two points at the deadline hour — before/after the extension drop — so
    # check the raw point list, not the deduplicated dict)
    wall_pts = next(s["points"] for s in d["series"] if s["name"] == "Wall-clock time")
    for h in (d["original_deadline_hour"], d["end_hour"]):
        if not any(p[0] == h and p[1] == 100.0 for p in wall_pts):
            failures.append(f"{name}: wall-clock does not hit 100 at h={h}")
    print(f"[collect-resource-curves] {name}: total ${d['total_cost']:.0f} API / ${d['total_runpod_cost']:.0f} GPU — ok" if not failures else f"[collect-resource-curves] {name}: checked")

if failures:
    print("FAIL:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("[collect-resource-curves] PASS")
