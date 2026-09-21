#!/usr/bin/env python3
"""Render the resource-timeline figure for one CRUX run.

A self-contained replica of the CRUX 2 resources LinesPlot: wall-clock time
and API spend as a share of what the run was allotted, against hours after
the first model call, with numbered phase chips above the plot and the phase
notes below it. Extra metered series (an experiment provider, GPU rental)
collected with --extra draw as further lines.

Inputs: the JSON from collect.py and, optionally, a sections file authored by
the analyst — [{"startHour": 0.0 | "start": "<ISO>", "label": "...",
"tip": "..."}] — one entry per phase, in order. Chip i sits at the middle of
phase i; the note reads "label — tip".

Usage:
  python3 build.py --data timeline.json [--sections sections.json]
      --out resource-timeline.png [--label "GPT-6 Astra · Codex CLI"] [--title ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lib"))
from figstyle import (esc, fmt_money, fmt_tokens, frame, hours_between, legend_html, lines_plot,  # noqa: E402
                      nice_step, notes_html, page, parse_ts, render)

WIDTH = 812  # figure width in CSS px; the 760-wide plot scales to the content box

SPEND_NAME = {"openai": "OpenAI API spend", "anthropic": "Anthropic API spend"}


def value_at(series: list, h: float) -> float:
    """Last observation at or before h (the series is cumulative per minute)."""
    v = 0.0
    for x, y in series:
        if x <= h:
            v = y
        else:
            break
    return v


def sample_hours(end: float) -> list[float]:
    step = nice_step(end, 36, candidates=(0.25, 0.5, 1, 2, 3, 4, 6, 12, 24))
    hs = [round(i * step, 3) for i in range(int(end / step) + 1)]
    if not hs or hs[-1] < end:
        hs.append(round(end, 3))
    return hs


def load_sections(path: str | None, start) -> list[dict]:
    if not path:
        return []
    raw = json.load(open(path, encoding="utf-8"))
    secs = raw["sections"] if isinstance(raw, dict) else raw
    out = []
    for s in secs:
        if not isinstance(s, dict) or "label" not in s:
            continue
        if "startHour" in s:
            h = float(s["startHour"])
        elif "start" in s:
            h = hours_between(start, parse_ts(s["start"]))
        else:
            raise SystemExit(f"section {s.get('label')!r} needs startHour or start")
        out.append({"startHour": h, "label": s["label"], "tip": s.get("tip", "")})
    return sorted(out, key=lambda s: s["startHour"])


def build(a: argparse.Namespace) -> None:
    d = json.load(open(a.data, encoding="utf-8"))
    start = parse_ts(d["start"])
    label = a.label or d.get("label") or "agent"
    end = float(d["end_hour"])
    limit = float(d["wall_limit_h"])
    budget = float(d["api_budget_usd"])
    cost = float(d["total_cost_usd"])
    tokens = float(d.get("total_tokens") or 0)

    hs = sample_hours(end)
    spend = d["series"]["spend"]
    series = [
        {"name": "Wall-clock time", "points": [[h, round(min(h / limit, 1.0) * 100, 1)] for h in hs],
         "endLabel": f"{end:.1f}h of {limit:g}h"},
        {"name": a.spend_name or SPEND_NAME.get((d.get("model") or "").split("/")[0], "API spend"),
         "points": [[h, round(value_at(spend, h) / budget * 100, 1)] for h in hs],
         "endLabel": f"{fmt_money(cost)} of {fmt_money(budget)}"},
    ]
    for x in d.get("extra", []):
        series.append({"name": x["name"],
                       "points": [[h, round(value_at(x["series"], h) / x["cap"] * 100, 1)] for h in hs],
                       "endLabel": f"{fmt_money(x['total'])} of {fmt_money(x['cap'])}"})

    secs = load_sections(a.sections, start)
    ann = []
    for i, s in enumerate(secs):
        nxt = secs[i + 1]["startHour"] if i + 1 < len(secs) else end
        text = esc(s["label"]) + (f" &mdash; {esc(s['tip'])}" if s.get("tip") else "")
        ann.append({"x": (s["startHour"] + nxt) / 2, "text": text})

    x_max = max(limit, end)
    x_step = nice_step(x_max, 12, candidates=(0.5, 1, 2, 3, 4, 6, 12, 24, 48, 72))
    hours_txt = f"{end:.1f} hours" if end < 48 else f"{end:.0f} hours"
    title = a.title or (f"In the {esc(label)} run, the agent used {round(cost / budget * 100)}% of the "
                        f"available API budget &mdash; {fmt_tokens(tokens)} tokens &mdash; over {hours_txt}")
    body = legend_html(series) + lines_plot(series, ann, x_max, x_step) + notes_html(ann)
    doc = page(WIDTH, frame(title, a.subtitle or "", body))
    w, h = render(doc, a.out, WIDTH, chrome=a.chrome, keep=a.keep)
    print(f"[build] {a.out}  {w}x{h}px  {len(series)} series, {len(ann)} phases"
          + ("" if secs else "  (no --sections: no phase chips)"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", required=True, help="timeline.json from collect.py")
    p.add_argument("--sections", help="phase sections JSON (see sections.template.json)")
    p.add_argument("--out", required=True, help="output PNG")
    p.add_argument("--label", help="run display name, e.g. 'GPT-6 Astra · Codex CLI'")
    p.add_argument("--title", help="override the figure title (HTML allowed)")
    p.add_argument("--subtitle", help="optional one-line subtitle under the title")
    p.add_argument("--spend-name", help="legend name for the API spend line")
    p.add_argument("--chrome", help="path to the Chrome binary (or set CHROME)")
    p.add_argument("--keep", action="store_true", help="keep the HTML work dir for debugging")
    build(p.parse_args())


if __name__ == "__main__":
    main()
