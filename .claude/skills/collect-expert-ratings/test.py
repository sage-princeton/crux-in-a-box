#!/usr/bin/env python3
"""Test collect.py against the two expert reviews published with CRUX 2.

Ground truth: the author reviews reproduced verbatim in
content/crux/crux-2.md (Appendix: "Reviews from paper authors") and the
published expert-ratings table (Personas 2/1/2/3, overall 2/6, conf 4;
TabPFN 1/2/2/2, overall 1/6, conf 5).
Run from the repo root: python3 .claude/skills/collect-expert-ratings/test.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parents[3]
COLLECT = Path(__file__).parent / "collect.py"
ARTICLE = REPO / "content" / "crux" / "crux-2.md"

EXPECTED: Dict[str, Any] = {
    "TabPFN": {
        "criteria": {"quality": 1, "clarity": 2, "significance": 2, "originality": 2},
        "overall": 1, "overall_label": "Strong Reject", "confidence": 5,
    },
    "Personas": {
        "criteria": {"quality": 2, "clarity": 1, "significance": 2, "originality": 3},
        "overall": 2, "overall_label": "Reject", "confidence": 4,
    },
}

text = ARTICLE.read_text(encoding="utf-8")
failures = []
for name, want in EXPECTED.items():
    marker = f"#### {name} review from paper authors"
    if marker not in text:
        failures.append(f"{name}: review block not found in {ARTICLE.name}")
        continue
    block = text.split(marker, 1)[1]
    # the block ends at the next h3/h4 review or appendix heading
    for stop in ("\n#### ", "\n### "):
        if stop in block:
            block = block.split(stop, 1)[0]
    tmp = Path(tempfile.gettempdir()) / f"crux-expert-{name}.md"
    tmp.write_text(block, encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(COLLECT), "--review", str(tmp)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        failures.append(f"{name}: collect.py failed: {out.stderr.strip()[:150]}")
        continue
    got = json.loads(out.stdout)
    if got["criteria"] != want["criteria"]:
        failures.append(f"{name}: criteria {got['criteria']} != {want['criteria']}")
    if got["overall"] != want["overall"] or got["confidence"] != want["confidence"]:
        failures.append(
            f"{name}: overall/conf {got['overall']}/{got['confidence']} != "
            f"{want['overall']}/{want['confidence']}"
        )
    if want["overall_label"].lower() not in (got["overall_label"] or "").lower():
        failures.append(f"{name}: label {got['overall_label']!r} != {want['overall_label']!r}")
    print(f"[collect-expert-ratings] {name}: criteria={got['criteria']} overall={got['overall']} conf={got['confidence']}")

if failures:
    print("FAIL:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("[collect-expert-ratings] PASS")
