#!/usr/bin/env python3
"""Extract criterion ratings from a NeurIPS-template expert review (stdlib).

Parses a markdown review written against the NeurIPS template (the format the
original paper authors use for shadow evaluations) and emits the data behind
the expert-ratings table: per-criterion 1-4 scores, the selected overall
score (1-6), and the selected confidence (1-5).

Recognized structures:
  - a criterion table row:  | Quality | 2 | one-line justification |
  - a selected option:      - **✓ 1 — Strong Reject.** ... (Selected)
    (also accepts [x]-marked checkbox lists)

Usage: python3 collect.py --review <review.md>
"""

from __future__ import annotations

import argparse
import json
import re

CRITERIA = ("quality", "clarity", "significance", "originality")
ROW_RE = re.compile(r"^\|\s*([A-Za-z]+)\s*\|\s*(\d)\s*\|", re.M)
SELECTED_RE = re.compile(
    r"^-\s*(?:\*\*)?(?:✓|\[x\])\s*(\d)\s*[—–-]+\s*([A-Za-z][A-Za-z ]*?)[.*]", re.M
)


def parse(text: str) -> dict:
    out: dict = {"criteria": {}, "overall": None, "overall_label": None,
                 "confidence": None}
    for name, score in ROW_RE.findall(text):
        if name.lower() in CRITERIA:
            out["criteria"][name.lower()] = int(score)
    # Selected options appear in document order: Overall Score before
    # Confidence. Overall is on a 6-point scale, confidence on 5.
    selections = SELECTED_RE.findall(text)
    for num, label in selections:
        n = int(num)
        if out["overall"] is None:
            out["overall"], out["overall_label"] = n, label.strip()
        elif out["confidence"] is None:
            out["confidence"] = n
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--review", required=True)
    args = p.parse_args()
    text = open(args.review, encoding="utf-8").read()
    result = parse(text)
    missing = [c for c in CRITERIA if c not in result["criteria"]]
    if missing or result["overall"] is None or result["confidence"] is None:
        raise SystemExit(
            f"incomplete parse: missing criteria={missing}, "
            f"overall={result['overall']}, confidence={result['confidence']}"
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
