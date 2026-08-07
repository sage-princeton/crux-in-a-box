#!/usr/bin/env python3
"""Verify the expert-vs-self-review crosswalk quotes are verbatim (stdlib).

The crosswalk table (app/data/reviewCrosswalk.ts) pairs a quote from the
human expert's review with a quote from the agent's final self-review, per
criticism. Curating it is judgment work; this script checks the one thing
that must hold mechanically: every quote fragment appears VERBATIM in its
source document (fragments are the pieces between "…" elisions).

Usage:
  python3 verify.py --data app/data/reviewCrosswalk.ts \
    --source personas:expert=<file> --source personas:self=<file> \
    --source tabpfn:expert=<file>   --source tabpfn:self=<file>
Exit 0 if every fragment matches; prints each miss otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys


def normalize(s: str) -> str:
    """Case-fold and unify quotes/dashes/whitespace so typography never
    causes a false mismatch."""
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'),
                 ("”", '"'), ("—", "-"), ("–", "-"),
                 (" ", " ")):
        s = s.replace(a, b)
    s = s.replace("*", "").replace("_", "")
    # quote-nesting and citation style are typography, not content
    s = s.replace('"', "'")
    s = re.sub(r"et\.\s*al\.?", "et al.", s)
    return re.sub(r"\s+", " ", s).lower()


QUOTE_RE = re.compile(
    r'(expertQuote|selfReviewQuote):\s*\n?\s*"((?:[^"\\]|\\.)*)"', re.S
)
SLUG_RE = re.compile(r'^\s{2}(\w+):\s*\{', re.M)


def parse_data(path: str) -> list[tuple[str, str, str]]:
    """(slug, kind, quote) triples from reviewCrosswalk.ts, in file order."""
    text = open(path, encoding="utf-8").read()
    # split per top-level slug block to attribute quotes to the right run
    slugs = [(m.group(1), m.start()) for m in SLUG_RE.finditer(text)]
    out = []
    for m in QUOTE_RE.finditer(text):
        kind = "expert" if m.group(1) == "expertQuote" else "self"
        owner = ""
        for slug, pos in slugs:
            if pos < m.start():
                owner = slug
        quote = m.group(2).replace('\\"', '"').replace("\\'", "'")
        out.append((owner, kind, quote))
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--source", action="append", default=[],
                   metavar="slug:kind=path", help="e.g. personas:expert=review.md")
    args = p.parse_args()

    sources: dict[tuple[str, str], str] = {}
    for spec in args.source:
        key, path = spec.split("=", 1)
        slug, kind = key.split(":")
        sources[(slug, kind)] = normalize(open(path, encoding="utf-8").read())

    quotes = parse_data(args.data)
    if not quotes:
        raise SystemExit(f"no quotes parsed from {args.data}")

    misses = []
    checked = 0
    for slug, kind, quote in quotes:
        src = sources.get((slug, kind))
        if src is None:
            continue  # no source supplied for this run/kind
        for frag in re.split(r"…|\.\.\.", quote):
            frag = frag.strip(" \"'")
            if len(frag) < 8:
                continue  # too short to be meaningful
            checked += 1
            if normalize(frag) not in src:
                misses.append(f"{slug}/{kind}: fragment not found verbatim: {frag[:70]!r}")

    print(f"[verify-review-crosswalk] {checked} fragments checked, {len(misses)} misses")
    for miss in misses:
        print(" -", miss)
    sys.exit(1 if misses else 0)


if __name__ == "__main__":
    main()
