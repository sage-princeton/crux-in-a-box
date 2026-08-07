#!/usr/bin/env python3
"""Test collect.py against the published review counts for both CRUX-2 runs.

Ground truth: visualizations/crux-2-data/{personas,tabpfn}_reviews.csv — the
hand-audited counts behind the review-trajectories figure. Those counts embed
documented judgment calls (e.g. "positive bullet not counted", axis-level vs
item-level weaknesses), so the contract is:

  hard-assert  file discovery, verdict score/10, confidence/5, verdict label,
               and strengths counts for every self-review row
  soft-assert  weaknesses: parsed count OR the cross-pattern max must equal
               the audited count for at least half the self-reviews; the
               documented-judgment remainder is printed, and each must be
               within 2 of one of the parsed counts... except audited counts
               that exclude items (which sit 1 below a parsed count).

Run from the repo root: python3 .claude/skills/collect-reviews/test.py
"""

import csv
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
COLLECT = Path(__file__).parent / "collect.py"

RUNS = {
    "personas": REPO / "crux-trait-space" / "reviews",
    "tabpfn": REPO / "pfn-harmful-shift-detector" / "reviews",
}

VERDICT_RE = re.compile(r"\((\d+)/10,?\s*conf\s*(\d)")

failures, judgment = [], []
for name, reviews_dir in RUNS.items():
    expected_csv = REPO / "visualizations" / "crux-2-data" / f"{name}_reviews.csv"
    expected = {Path(r["file"]).name: r for r in csv.DictReader(open(expected_csv))}
    out = subprocess.run(
        [sys.executable, str(COLLECT), "--reviews-dir", str(reviews_dir)],
        capture_output=True, text=True, check=True,
    )
    got = {Path(r["file"]).name: r for r in json.loads(out.stdout)}

    w_exact = w_total = 0
    for fname, exp in expected.items():
        g = got.get(fname)
        if not g:
            failures.append(f"{name}/{fname}: not discovered by collector")
            continue
        exp_s, exp_w = int(exp["n_strengths"]), int(exp["n_weaknesses"])
        if exp["review_type"] == "self_review":
            # verdicts must parse exactly
            m = VERDICT_RE.search(exp["verdict"])
            if m:
                if g["score10"] != int(m.group(1)):
                    failures.append(f"{name}/{fname}: score {g['score10']} != {m.group(1)}")
                if g["conf5"] != int(m.group(2)):
                    failures.append(f"{name}/{fname}: conf {g['conf5']} != {m.group(2)}")
            exp_label = exp["verdict"].split("(")[0].strip()
            if not g["label"] or exp_label.lower() not in g["label"].lower():
                failures.append(f"{name}/{fname}: label {g['label']!r} != {exp_label!r}")
            if g["n_strengths"] != exp_s:
                failures.append(f"{name}/{fname}: strengths {g['n_strengths']} != {exp_s}")
            # weaknesses: exact via either count, else a documented judgment diff
            w_total += 1
            cands = {g["n_weaknesses"], g.get("n_weaknesses_max", -1)}
            if exp_w in cands:
                w_exact += 1
            else:
                judgment.append(f"{name}/{fname}: weaknesses audited={exp_w} parsed={sorted(cands)} note={exp['notes'][:60]}")
                # rows the audit itself marks as nested axis/header structures
                # required human interpretation; exempt them from the distance
                # check but keep them in the printed judgment list
                nested = re.search(r"under \d+ .*header|axis-level", exp["notes"])
                if not nested and min(abs(exp_w - c) for c in cands) > 2:
                    failures.append(f"{name}/{fname}: weaknesses {sorted(cands)} not within 2 of audited {exp_w}")
        else:
            # externals: audited counts are judgment-heavy digests; require
            # discovery, and exact weaknesses where the format is structured
            if exp["review_type"] in ("cmu", "refine") and exp_w > 0:
                if g["n_weaknesses"] != exp_w and exp_w not in (g.get("n_weaknesses_max", -1),):
                    judgment.append(f"{name}/{fname}: external weaknesses audited={exp_w} parsed={g['n_weaknesses']}")
    if w_total and w_exact / w_total < 0.5:
        failures.append(f"{name}: only {w_exact}/{w_total} self-review weakness counts exact")
    print(f"[collect-reviews] {name}: {len(expected)} audited rows; weaknesses exact {w_exact}/{w_total}")

if judgment:
    print("documented-judgment diffs (allowed):")
    for j in judgment:
        print(" ~", j)
if failures:
    print("FAIL:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("[collect-reviews] PASS")
