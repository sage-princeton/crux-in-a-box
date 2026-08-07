#!/usr/bin/env python3
"""Collect review-trajectory data from a CRUX run workspace (stdlib only).

Walks reviews/ for self-reviews (blind_round_*.md, final_review.md,
final_blind.md) and reviews/external/ for external AI reviews, and emits one
row per review: strengths/weaknesses/limitations counts + verdict. Feeds the
review-trajectories figure (crux2-*-reviews in app/data/plots.ts).

Counting heuristics (deterministic first pass — a human/agent applies the
judgment rules in SKILL.md before publishing):
  - Sections are located by `## N. Strengths / Weaknesses / Limitations`
    headings (case-insensitive, any numbering).
  - Items are counted as, in priority order: lettered `**(a)`-style blocks,
    reviewer-numbered `W1`-style blocks, top-level `- ` bullets, numbered
    `1.` items, bold-led `**Title` paragraphs.
  - Verdict = `**N / 10 — Label**` in the Score section; confidence =
    `**N / 5` in the Confidence section; falls back to a
    `Recommendation: ...` line.

Usage: python3 collect.py --reviews-dir <run>/reviews [--json|--csv]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

SELF_PATTERNS = ("blind_round_*.md", "final_review.md", "final_blind.md")
SECTION_RE = re.compile(r"^##+\s*\d*\.?\s*(.+)$")

ITEM_PATTERNS = [
    ("lettered", re.compile(r"^\*\*?\(([a-z])\)", re.M)),
    ("w-numbered", re.compile(r"^(?:\*\*)?W(\d+)", re.M)),
    ("dash", re.compile(r"^- ", re.M)),
    ("numbered", re.compile(r"^\d+\.\s", re.M)),
    ("bold-led", re.compile(r"^\*\*[A-Z“\"]", re.M)),
    # any bold-led block, including parenthetical tags like **(d/e statistics)
    ("bold-any", re.compile(r"^\*\*\S", re.M)),
]

SCORE_RE = re.compile(r"(\d+)\s*/\s*10\s*[—–-]+\s*[\"“]?([A-Za-z][A-Za-z ]*?)[.:,*\"”]")
SCORE_NUM_RE = re.compile(r"(\d+)\s*/\s*10")
CONF_RE = re.compile(r"(\d)\s*/\s*5")
RECO_RE = re.compile(r"^Recommendation:\s*(.+?)\s*$", re.M)


def split_sections(text: str) -> dict[str, str]:
    """Map lowercase section-title keyword -> section body."""
    sections: dict[str, str] = {}
    current, buf = None, []
    for line in text.splitlines():
        m = SECTION_RE.match(line)
        if m:
            if current:
                sections[current] = "\n".join(buf)
            title = m.group(1).lower()
            for key in ("strength", "weakness", "limitation", "score", "confidence"):
                if key in title:
                    current = key
                    break
            else:
                current = None
            buf = []
        elif current:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf)
    return sections


def count_items(body: str) -> tuple[int, str]:
    """Count top-level items using the highest-priority pattern that fires
    strongly (>=3 for structured labels, >=1 for bullets)."""
    for name, pat in ITEM_PATTERNS:
        n = len(pat.findall(body))
        if n >= (3 if name in ("lettered", "w-numbered") else 1):
            return n, name
    return 0, "none"


def parse_review(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    sections = split_sections(text)
    row: dict = {"file": str(path)}
    for key, out in (("strength", "n_strengths"), ("weakness", "n_weaknesses"),
                     ("limitation", "n_limitations")):
        body = sections.get(key, "")
        n, how = count_items(body)
        row[out] = n
        row[out + "_how"] = how
        if key == "weakness":
            # axis-level vs item-level counts differ in some formats; report
            # the max across patterns so the judgment pass can pick
            row["n_weaknesses_max"] = max(
                (len(pat.findall(body)) for _, pat in ITEM_PATTERNS), default=0
            )
    score_body = sections.get("score", "")
    conf_body = sections.get("confidence", "")
    score = SCORE_RE.search(score_body) or SCORE_RE.search(text)
    # fallback for "**4 — Borderline reject.**"-style scores without "/10"
    score_num = SCORE_NUM_RE.search(score_body) or re.search(
        r"\*\*(\d+)\s*[—–-]", score_body
    )
    conf = CONF_RE.search(conf_body) or re.search(r"\*\*(\d)\s*[—–-]", conf_body)
    reco = RECO_RE.search(text)
    row["score10"] = (int(score.group(1)) if score
                      else int(score_num.group(1)) if score_num else None)
    label = None
    if reco:
        label = reco.group(1)
    elif score:
        label = score.group(2).strip()
    elif re.search(r"recommend acceptance", text, re.I):
        label = "Accept"
    row["label"] = label
    row["conf5"] = int(conf.group(1)) if conf else None
    return row


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--reviews-dir", required=True)
    p.add_argument("--csv", action="store_true", help="emit CSV instead of JSON")
    args = p.parse_args()
    root = Path(args.reviews_dir)

    self_files: list[Path] = []
    for pat in SELF_PATTERNS:
        self_files += sorted(root.glob(pat))
    external = sorted((root / "external").glob("*.md")) if (root / "external").is_dir() else []

    rows = []
    for f in self_files:
        rows.append({"review_type": "self_review", **parse_review(f)})
    for f in external:
        kind = ("stanford" if "stanford" in f.name else
                "cmu" if "cmu" in f.name else
                "refine" if "refine" in f.name else "external")
        row = {"review_type": kind, **parse_review(f)}
        text = f.read_text(encoding="utf-8", errors="replace")
        if kind == "refine":
            n = len(re.findall(r"^### \d+\.", text, re.M))
            if n:
                row["n_weaknesses"], row["n_weaknesses_how"] = n, "refine-comments"
        if kind == "cmu":
            n = len(re.findall(r"^## Item \d+", text, re.M))
            if n:
                row["n_weaknesses"], row["n_weaknesses_how"] = n, "cmu-items"
        rows.append(row)

    if args.csv:
        w = csv.DictWriter(io.StringIO(), fieldnames=list(rows[0].keys()))
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
        sys.stdout.write(out.getvalue())
    else:
        print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
