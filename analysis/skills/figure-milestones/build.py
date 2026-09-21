#!/usr/bin/env python3
"""Render the milestone dumbbell figure: planned deadline vs actual completion.

Two panels in the CRUX 2 palette. The time panel puts each milestone on an
hours-after-launch axis with a hollow marker at the planned deadline and a
filled one at the actual completion, joined by a bar (orange when late, teal
when early). The spend panel repeats the rows on a dollars axis: the API
spend the run had consumed at each of those moments.

Input: milestones.json from collect.py.

Usage:
  python3 build.py --data milestones.json --out milestones.png [--label ...] [--no-spend]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lib"))
from figstyle import GRIDLINE, INK, MUTED, SERIES, esc, fmt_money, frame, nice_step, page, render  # noqa: E402

WIDTH = 812
W = 760
PAD_L, PAD_R, PAD_T, PAD_B, ROW = 296, 32, 14, 40, 34
LATE, EARLY, PLANNED = SERIES[1], SERIES[0], INK


def trunc(s: str, n: int = 40) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def dumbbell_svg(rows: list[dict], key_p: str, key_a: str, x_max: float, x_step: float,
                 x_label: str, fmt) -> str:
    H = PAD_T + len(rows) * ROW + PAD_B

    def X(v):
        return PAD_L + v / x_max * (W - PAD_L - PAD_R)

    b = []
    t = 0.0
    while t <= x_max + 1e-9:
        b.append(f'<line x1="{X(t):.1f}" y1="{PAD_T}" x2="{X(t):.1f}" y2="{H - PAD_B}" stroke="{GRIDLINE}"/>')
        b.append(f'<text x="{X(t):.1f}" y="{H - PAD_B + 18}" text-anchor="middle" fill="{INK}" font-size="12">{fmt(t)}</text>')
        t += x_step
    b.append(f'<text x="{(PAD_L + W - PAD_R) / 2:.0f}" y="{H - 6}" text-anchor="middle" fill="{INK}" '
             f'font-size="12.5" font-weight="600">{esc(x_label)}</text>')
    for i, r in enumerate(rows):
        cy = PAD_T + i * ROW + ROW / 2
        b.append(f'<text x="{PAD_L - 14}" y="{cy:.1f}" text-anchor="end" dominant-baseline="central" fill="{INK}" '
                 f'font-size="12.5" font-weight="600">M{r["num"]} · {esc(trunc(r["label"]))}</text>')
        pv, av = r.get(key_p), r.get(key_a)
        if pv is None:
            b.append(f'<text x="{PAD_L}" y="{cy:.1f}" dominant-baseline="central" fill="{MUTED}" font-size="11.5">no deadline recorded</text>')
            continue
        px = X(min(pv, x_max))
        if av is not None:
            ax = X(min(av, x_max))
            col = LATE if av > pv else EARLY
            b.append(f'<line x1="{px:.1f}" y1="{cy:.1f}" x2="{ax:.1f}" y2="{cy:.1f}" stroke="{col}" stroke-width="5" '
                     f'stroke-linecap="round" opacity="0.45"/>')
        b.append(f'<circle cx="{px:.1f}" cy="{cy:.1f}" r="6" fill="#fff" stroke="{PLANNED}" stroke-width="1.75"/>')
        # value labels sit to the right of the rightmost marker, or to the
        # left when that would run past the plot's right edge
        right_edge = W - PAD_R
        if av is not None:
            col = LATE if av > pv else EARLY
            b.append(f'<circle cx="{ax:.1f}" cy="{cy:.1f}" r="6" fill="{col}"/>')
            text = fmt(av)
            if max(px, ax) + 11 + len(text) * 7 > right_edge:
                lx, anchor = min(px, ax) - 11, "end"
            else:
                lx, anchor = max(px, ax) + 11, "start"
            b.append(f'<text x="{lx:.1f}" y="{cy:.1f}" text-anchor="{anchor}" dominant-baseline="central" fill="{col}" '
                     f'font-size="11.5" font-weight="700" stroke="#fff" stroke-width="3.5" paint-order="stroke">{text}</text>')
        else:
            text = f"planned {fmt(pv)} · not completed"
            if px + 11 + len(text) * 6.2 > right_edge:
                lx, anchor = px - 11, "end"
            else:
                lx, anchor = px + 11, "start"
            b.append(f'<text x="{lx:.1f}" y="{cy:.1f}" text-anchor="{anchor}" dominant-baseline="central" fill="{MUTED}" '
                     f'font-size="11.5">{text}</text>')
    return f'<svg viewBox="0 0 {W} {H}" style="display:block;width:100%;overflow:visible">' + "".join(b) + "</svg>"


def build(a: argparse.Namespace) -> None:
    d = json.load(open(a.data, encoding="utf-8"))
    rows = d["milestones"]
    n = len(rows)
    done = [r for r in rows if r.get("actual_hours") is not None and r.get("planned_hours") is not None]
    late = sum(1 for r in done if r["actual_hours"] > r["planned_hours"])
    limit = d.get("wall_limit_h")
    hmax = max([limit or 0] + [r.get("planned_hours") or 0 for r in rows] + [r.get("actual_hours") or 0 for r in rows])
    x_step = nice_step(hmax, 12, candidates=(0.5, 1, 2, 3, 4, 6, 12, 24, 48, 72))
    x_max = max(hmax, x_step) * 1.02
    legend = (f'<ul class="legend"><li style="color:{PLANNED}"><span style="display:inline-block;width:12px;height:12px;'
              f'border-radius:99px;border:1.75px solid {PLANNED};background:#fff"></span>Planned deadline</li>'
              f'<li style="color:{EARLY}"><span style="display:inline-block;width:12px;height:12px;border-radius:99px;'
              f'background:{EARLY}"></span>Completed before the deadline</li>'
              f'<li style="color:{LATE}"><span style="display:inline-block;width:12px;height:12px;border-radius:99px;'
              f'background:{LATE}"></span>Completed after it</li></ul>')
    panels = [f'<div style="font-size:14px;font-weight:600;color:{INK};margin-top:10px">Hours after launch</div>'
              + dumbbell_svg(rows, "planned_hours", "actual_hours", x_max, x_step, "Hours after launch", lambda v: f"{v:g}")]
    budget = d.get("api_budget_usd")
    has_spend = any(r.get("spend_at_planned") is not None for r in rows)
    if has_spend and not a.no_spend:
        smax = max([budget or 0] + [r.get("spend_at_planned") or 0 for r in rows] + [r.get("spend_at_actual") or 0 for r in rows])
        s_step = nice_step(smax, 10, candidates=(10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000))
        panels.append(f'<div style="font-size:14px;font-weight:600;color:{INK};margin-top:18px">API spend at that moment</div>'
                      + dumbbell_svg(rows, "spend_at_planned", "spend_at_actual", max(smax, s_step) * 1.02, s_step,
                                     "API spend, USD", lambda v: fmt_money(v)))
    label = a.label
    title = a.title or "Milestones: planned deadline vs actual completion"
    subtitle = a.subtitle
    if subtitle is None:
        src = esc(d.get("plan", "PLAN.md"))
        subtitle = ((f"{esc(label)} — " if label else "")
                    + f"The {n} milestones the agent scheduled in its plan ({src}) and when each was actually reached, "
                    f"from the plan’s status cells and the analyst’s evidence-backed timestamps. "
                    + (f"{late} of {len(done)} completed milestones came in after their deadline." if done else "No completion times recorded."))
    doc = page(WIDTH, frame(title, subtitle, legend + "".join(panels)))
    w, h = render(doc, a.out, WIDTH, chrome=a.chrome, keep=a.keep)
    print(f"[build] {a.out}  {w}x{h}px  {n} milestones, {len(done)} with actuals, {late} late, {len(panels)} panel(s)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--label", help="run display name for the title")
    p.add_argument("--title")
    p.add_argument("--subtitle", help="override the generated subtitle ('' for none)")
    p.add_argument("--no-spend", action="store_true", help="omit the spend panel")
    p.add_argument("--chrome")
    p.add_argument("--keep", action="store_true")
    build(p.parse_args())


if __name__ == "__main__":
    main()
