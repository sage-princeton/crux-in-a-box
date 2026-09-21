#!/usr/bin/env python3
"""Test the shared renderer: the PNG must be exactly the figure, at 2x.

Renders two pages of known CSS height — one shorter than any plausible
viewport (300px) and one taller (1500px) — and checks the PNG is 2x the
body size in both directions. This is the check that the measuring pass
reports the content's height, never the viewport's.

Run from the repo root: python3 analysis/skills/_lib/test.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from figstyle import find_chrome, fmt_hours, fmt_money, fmt_tokens, nice_step, page, parse_ts, render  # noqa: E402

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)


# pure helpers (no Chrome)
check(fmt_money(8458.33) == "$8,458" and fmt_money(153.14) == "$153.14" and fmt_money(0.55) == "$0.55"
      and fmt_money(0) == "$0", f"fmt_money {fmt_money(8458.33)} {fmt_money(153.14)} {fmt_money(0.55)} {fmt_money(0)}")
check(fmt_tokens(191_700_801) == "191.7 million" and fmt_tokens(4_222_906_084) == "4.2 billion", "fmt_tokens")
check(fmt_hours(9.15) == "9.2h" and fmt_hours(164.52) == "165h", f"fmt_hours {fmt_hours(9.15)} {fmt_hours(164.52)}")
check(nice_step(168, 12, candidates=(0.5, 1, 2, 3, 4, 6, 12, 24, 48, 72)) == 24, "nice_step 168h")
check(nice_step(10, 12, candidates=(0.5, 1, 2, 3, 4, 6, 12, 24, 48, 72)) == 1, "nice_step 10h")
check(parse_ts("2026-09-03 20:10 UTC").isoformat() == "2026-09-03T20:10:00+00:00", "parse_ts 'UTC' form")
check(parse_ts("2026-07-02T16:00Z").isoformat() == "2026-07-02T16:00:00+00:00", "parse_ts Z form")
print("[figstyle] helpers ok" if not failures else "[figstyle] helpers checked")

try:
    find_chrome()
    with tempfile.TemporaryDirectory(prefix="crux-fig-test-") as td:
        for css_h in (300, 1500):
            doc = page(500, f'<figure class="fig" style="height:{css_h}px;padding:0;border:0">x</figure>')
            out = str(Path(td) / f"h{css_h}.png")
            w, h = render(doc, out, 500)
            check((w, h) == (1000, css_h * 2), f"{css_h}px body rendered as {w}x{h}, expected 1000x{css_h * 2}")
            print(f"[figstyle] render {css_h}px -> {w}x{h}")
except RuntimeError as e:
    print(f"[figstyle] SKIP render: {e}")

if failures:
    print("FAIL:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("[figstyle] PASS")
