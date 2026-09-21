#!/usr/bin/env python3
"""Render the referee-rounds figure.

The CRUX 2 reviews-figure style: one pane per run, rows in time order, each
row carrying the referee's facet ratings as cells on the teal 1–4 ramp
(primary-50/300/400/700) and the Overall recommendation in the verdict column.

Input: one or more reviews.json files from collect.py (several stack as
panes). Facet columns are the union of the facets found, in first-seen order;
a facet scored on a scale other than 1–4 is binned onto the ramp and the key
says so.

Usage:
  python3 build.py --data reviews.json [--data other.json] --out referee-rounds.png
      [--label "GPT-6 Astra · Codex CLI"] [--title ...] [--subtitle ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lib"))
from figstyle import INK, MUTED, RAMP4, RAMP4_INK, WEAK, esc, frame, page, render  # noqa: E402

SHORT = {"soundness": "sound.", "presentation": "present.", "contribution": "contrib.",
         "identification": "identif.", "measurement": "measure.", "inference": "infer.",
         "policy relevance": "policy", "reproducibility": "reprod.", "craft": "craft",
         "quality": "quality", "clarity": "clarity", "significance": "signif.", "originality": "orig."}
EDGE, LABEL_W, CELL_W, CELL_GAP, VERDICT_W = 8, 74, 60, 4, 176
ROW, CELL_H, HEAD_BAND, PANE_HEAD, FACET_ROW, PANE_GAP = 30, 24, 30, 34, 24, 18


def short(name: str) -> str:
    k = name.lower()
    return SHORT.get(k, (k[:7] + ".") if len(k) > 8 else k)


def binned(score: int | None, scale: int | None) -> int | None:
    if score is None:
        return None
    if not scale or scale == 4:
        return max(1, min(4, int(score)))
    return max(1, min(4, round(score / scale * 4)))


def grid_svg(groups: list[dict], facets: list[str], labels: dict[str, str], overall_scale: int) -> tuple[str, int, int]:
    n = len(facets)
    grid_x = EDGE + LABEL_W + 10
    grid_w = n * CELL_W + (n - 1) * CELL_GAP
    W = grid_x + grid_w + 10 + VERDICT_W + EDGE
    # With few facet columns the pane can be narrower than its own title;
    # widen it (the verdict column is right-aligned, so it simply moves out).
    longest = max((len(g["name"]) for g in groups), default=0)
    W = max(W, int(longest * 9.2) + 2 * EDGE + 24)

    def cell_x(j):
        return grid_x + j * (CELL_W + CELL_GAP)

    b = [f'<text x="{EDGE + 2}" y="19" fill="{MUTED}" font-size="13" font-weight="600">Time ↓</text>',
         f'<text x="{grid_x + grid_w / 2:.1f}" y="19" text-anchor="middle" fill="{INK}" font-size="14" '
         f'font-weight="700">Referee facet scores, 1–4</text>',
         f'<text x="{W - EDGE - 2}" y="19" text-anchor="end" fill="{MUTED}" font-size="13" font-weight="600">Verdict</text>']
    y = HEAD_BAND
    for g in groups:
        rows_h = FACET_ROW + len(g["items"]) * ROW
        b.append(f'<rect x="{EDGE}" y="{y}" width="{W - EDGE * 2}" height="{PANE_HEAD + rows_h}" fill="none" stroke="{INK}"/>')
        b.append(f'<text x="{EDGE + 10}" y="{y + 22}" fill="{INK}" font-size="16.5" font-weight="700">{esc(g["name"])}</text>')
        fy = y + PANE_HEAD + FACET_ROW - 9
        for j, f in enumerate(facets):
            b.append(f'<text x="{cell_x(j) + CELL_W / 2:.1f}" y="{fy}" text-anchor="middle" fill="{MUTED}" '
                     f'font-size="10.5" font-weight="600">{esc(short(labels.get(f, f)))}</text>')
        rows_y = y + PANE_HEAD + FACET_ROW
        for i, it in enumerate(g["items"]):
            cy = rows_y + i * ROW + ROW / 2
            b.append(f'<text x="{EDGE + LABEL_W}" y="{cy}" text-anchor="end" dominant-baseline="central" '
                     f'fill="{INK}" font-size="13" font-weight="600">{esc(it["label"])}</text>')
            for j, f in enumerate(facets):
                s = it["scores"].get(f)
                x = cell_x(j)
                if s is None:
                    b.append(f'<text x="{x + CELL_W / 2:.1f}" y="{cy}" text-anchor="middle" dominant-baseline="central" '
                             f'fill="{MUTED}" font-size="13">·</text>')
                    continue
                b.append(f'<rect x="{x}" y="{cy - CELL_H / 2:.1f}" width="{CELL_W}" height="{CELL_H}" rx="3" fill="{RAMP4[s]}"/>')
                b.append(f'<text x="{x + CELL_W / 2:.1f}" y="{cy}" text-anchor="middle" dominant-baseline="central" '
                         f'fill="{RAMP4_INK[s]}" font-size="13" font-weight="700">{it["raw"].get(f, s)}</text>')
            b.append(f'<text x="{W - EDGE - 8}" y="{cy}" text-anchor="end" dominant-baseline="central" fill="{WEAK}" '
                     f'font-size="12.5" font-weight="700">{esc(it["verdict"])}</text>')
        y += PANE_HEAD + rows_h + PANE_GAP
    H = y - PANE_GAP + EDGE
    return (f'<svg viewBox="0 0 {W} {H}" style="display:block;width:{W}px;margin-top:12px">' + "".join(b) + "</svg>", W, H)


def build(a: argparse.Namespace) -> None:
    datas = [json.load(open(p, encoding="utf-8")) for p in a.data]
    labels_cli = a.label or []
    facets: list[str] = []
    facet_labels: dict[str, str] = {}
    for d in datas:
        for f in d.get("facet_order", []):
            if f not in facets:
                facets.append(f)
                facet_labels[f] = d.get("facet_labels", {}).get(f, f)
    if not facets:
        raise SystemExit("no facets in the collected data")
    overall_scale = next((d.get("overall_scale") for d in datas if d.get("overall_scale")), 6)
    facet_scale = next((d.get("facet_scale") for d in datas if d.get("facet_scale")), 4)

    groups = []
    all_rounds = []
    for k, d in enumerate(datas):
        name = labels_cli[k] if k < len(labels_cli) else (d.get("label") or f"run {k + 1}")
        items = []
        for r in sorted(d["rounds"], key=lambda r: (r.get("time") or "", r["round"])):
            scores = {f: binned(r["facets"].get(f), r.get("facet_scale")) for f in facets}
            raw = {f: r["facets"].get(f) for f in facets if r["facets"].get(f) is not None}
            ov, sc, lab = r.get("overall"), r.get("overall_scale") or overall_scale, r.get("overall_label")
            verdict = (f"{lab} ({ov}/{sc})" if lab and ov is not None else
                       f"({ov}/{sc})" if ov is not None else (lab or "·"))
            # one decimal even on long runs: rounds an hour apart must stay distinct
            label = f"T+{r['hour']:.1f}h" if r.get("hour") is not None else f"R{r['round']}"
            items.append({"label": label, "scores": scores, "raw": raw, "verdict": verdict})
            all_rounds.append(r)
        groups.append({"name": f"{name} — {len(items)} referee rounds (chronological)", "items": items})

    svg, W, H = grid_svg(groups, facets, facet_labels, overall_scale)
    n = len(all_rounds)
    best = max((r["overall"] for r in all_rounds if r.get("overall") is not None), default=None)
    best_label = next((r.get("overall_label") for r in all_rounds if r.get("overall") == best), None)
    n_best = sum(1 for r in all_rounds if r.get("overall") == best)
    facet_phrase = ", ".join(facet_labels[f] for f in facets[:-1]) + (" and " if len(facets) > 1 else "") + facet_labels[facets[-1]]
    title = a.title or ("Every referee round, scored facet by facet" if len(datas) == 1 else
                        f"Every referee round across {len(datas)} runs, scored facet by facet")
    subtitle = a.subtitle
    if subtitle is None:
        subtitle = (f"An isolated same-model referee read only the compiled PDF each round and scored {esc(facet_phrase)} "
                    f"on a 1–{facet_scale} scale alongside an Overall recommendation (1–{overall_scale}).")
        if best is not None:
            subtitle += (f" Overall peaked at {best}/{overall_scale}" + (f" ({esc(best_label)})" if best_label else "")
                         + f" in {n_best} of {n} rounds.")
    key = "".join(f'<span class="cell" style="background:{RAMP4[s]};color:{RAMP4_INK[s]}">{s}</span>' for s in (1, 2, 3, 4))
    binned_note = "" if facet_scale == 4 else f" (scored 1–{facet_scale}, binned onto the ramp)"
    key_html = (f'<p class="key"><span style="display:inline-flex">{key}</span>facet score{binned_note} · '
                f"the verdict column is the separate Overall recommendation (1–{overall_scale}, {overall_scale} = best)</p>")
    width = W + 2 * 24 + 2
    doc = page(width, frame(title, subtitle, svg + key_html))
    w, h = render(doc, a.out, width, chrome=a.chrome, keep=a.keep)
    print(f"[build] {a.out}  {w}x{h}px  {len(datas)} pane(s), {n} rounds, facets {facets}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", action="append", required=True, help="reviews.json from collect.py (repeatable: stacked panes)")
    p.add_argument("--out", required=True)
    p.add_argument("--label", action="append", help="pane name per --data, in order (default: the JSON's label)")
    p.add_argument("--title")
    p.add_argument("--subtitle", help="override the generated subtitle ('' for none)")
    p.add_argument("--chrome")
    p.add_argument("--keep", action="store_true")
    build(p.parse_args())


if __name__ == "__main__":
    main()
