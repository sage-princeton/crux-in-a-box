# Playbook: Figures, Diagrams, and Formatting

Scope: visual artifacts only — plots, diagrams, tables, page formatting. Prose and narrative are `playbooks/writing.md`. Treat the specific values here as defaults that work, not suggestions to rediscover.

## The iteration loop (figures fail from missing feedback, not missing skill)

1. **Spec before code.** Each figure gets a 3-line spec in `figures/<name>/spec.md`: the *takeaway* (one sentence — what exactly should the reader conclude?), the locked caption, the source data artifact. Iteration without a spec is aimless; the spec is the convergence target.
2. **Plot from cached artifacts only.** One script per figure (`plot_<name>.py`), reading from `runs/...` JSON/CSV — never recomputing. `make figures` regenerates everything deterministically. An iteration cycle should cost seconds.
3. **Render, then LOOK.** After every change: render at the width the figure will occupy in the compiled PDF, open the image with the vision tool, critique against the spec. A figure nobody looked at has the same epistemic status as a number nobody re-derived. For the final pass, compile the actual PDF page and inspect the figure *in situ* — LaTeX scaling is where legible figures die.
4. **Blind takeaway test.** Before a figure is done: a fresh subagent gets *only the rendered image* (no caption, no context) and answers "what do you conclude, and what's confusing?" Stated takeaway matches the spec's → pass. Two consecutive passes = done.

## Matplotlib canon

Create a shared `figures/style.py` at the first drafting milestone; every plot script imports it. Two profiles: **paper** (design at ~8″ wide with large fonts — 18–20 pt axis labels, 16 pt ticks/legend — so LaTeX scales it down crisp at `\textwidth`) and **compact** (~5.5″, 8–9 pt) for exploratory/column figures. Baseline rc for both:

```python
rc = {
    "font.family": "serif",                     # match the paper body
    "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "axes.unicode_minus": False,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#888888", "axes.linewidth": 1.0,   # quiet gray frame
    "xtick.color": "#888888", "ytick.color": "#888888",
    "xtick.direction": "out", "ytick.direction": "out",
    "axes.grid": True, "axes.axisbelow": True,            # grid BEHIND data
    "grid.linestyle": "--", "grid.color": "gray",
    "grid.alpha": 0.35, "grid.linewidth": 0.8,
    "savefig.dpi": 300, "savefig.bbox": "tight", "savefig.pad_inches": 0.04,
    "pdf.fonttype": 42, "ps.fonttype": 42,    # TrueType, not Type-3 — venues require it
}
```

Rules that ride on top:

- **Dual-encode every category: color AND marker shape**, defined once in a single `CATEGORY → (color, marker)` map in style.py. The figure must survive greyscale printing (color disambiguates on screen, marker in print). Scatter markers large (`s≈200` at paper scale) with `edgecolor="white"`, drawn above the grid (`zorder=3`).
- **Save every figure as both PNG (300 dpi, for your own vision checks) and PDF (vector, what LaTeX embeds)** — one `save(fig, path)` helper writes both.
- **Tight bbox for standalone figures; fixed canvas for composites.** Multi-panel LaTeX layouts need sibling figures with *identical* bounding boxes — tight-cropping each one separately makes equal-width subfigures render at different scales. Keep a `save_fixed()` variant (`savefig.bbox=None, pad=0`) for those.
- **Readable ticks, never scientific notation**: format 250000 as `250k`, 2000000 as `2M`; on log scales, set an explicit curated tick list.
- **Legends are visually secondary**: frameless, horizontal, below the axes, ~2 pt smaller than body text; use proxy `Line2D` handles so glyphs render clean. If framed: light gray edge, sharp corners, no fancybox.
- **Annotate a curated subset, not everything.** Hand-pick the points the takeaway needs, label with curved arrows (`connectionstyle="arc3,rad=0.35"`) and white-backed text at reduced alpha; the rest is the caption's or a table's job. Pre-offset label positions yourself for crowded regions — never trust auto-placement.
- **If the y-axis doesn't start at zero, say so visually** (axis-break zigzag on the spine) *and* in the caption.
- **Panel labels** for multi-panel figures: bold lowercase letters at top-left, outside the axes (`ax.text(-0.12, 1.03, "a", transform=ax.transAxes, fontweight="bold")`).

## Figure-construction discipline

- One module of paper figures, separate from exploratory plots; **one function per figure, named for the claim it supports** (e.g. `fig_accuracy_by_method`), with the figure's narrative role in its docstring.
- **Canonical-cell constants**: when a figure compares categories, define once — as a named module constant with a comment — exactly which run/config represents each cell, so no figure silently mixes sweeps. Exclusions are figure-only filters with a documented reason, never mutations of source tables.
- A per-category *spread* (same metric across variants) reads better as a **dumbbell** (thin gray min→max segment per row, category dots on it) than as grouped bars.
- Keep square fixed-canvas variants of key figures for composite layouts and landscape variants for standalone use — same data, same style module, different geometry.

## LaTeX formatting

Preamble kit (add as needed, in this spirit): `booktabs`, `microtype`, `graphicx`, `enumitem`, `xcolor`, `tikz`, `tcolorbox`, `url` with `[hyphens]`, `hyperref`.

- **Figures**: `\begin{figure}[t] \centering ... \caption{...} \label{fig:...} \end{figure}`. Caption *below* figures, *above* tables. Width as a fraction of `\linewidth` — full for data figures, smaller (e.g. `0.6\linewidth`) when the content doesn't earn the width. Negative `\vspace` (e.g. `-.33cm`) after captions to recover space is legitimate — but it's the *last* pass, never structural.
- **Captions carry the reading instructions**: what the figure shows, how to read anything non-obvious (axis breaks, per-phase scales, aggregation), in italics if it's a usage note. Small inline legend chips can be defined as commands and used inside the caption itself so the caption is self-contained.
- **Tables**: booktabs only — `\toprule/\midrule/\bottomrule`, **no vertical rules**, `@{}` to trim outer padding, `>{\raggedright\arraybackslash}p{<width>}` columns for text-heavy tables. If a table is full of repeated identical values, the problem is the experiment design, not the table.
- **Diagrams native, not raster**: schematics, timelines, and taxonomy figures in TikZ/tcolorbox directly in the document — crisper, page-consistent fonts (`\scriptsize` inside diagrams), and editable under review. Conventions: `\definecolor` with named HTML colors at the top of the figure; node styles declared once in the `tikzpicture` options; background phase-shading as light fills (`color!4`–`!10`); gridlines `gray!18 very thin`; wrap in `\resizebox{\linewidth}{!}{...}` for width control.
- **Callout boxes** (recommendations, key claims): `tcolorbox` with near-white tinted backgrounds (`color!4`), zero or hairline frames, or a left borderline only — plus a counter if the boxes form a series. Subtle beats loud; the box is wayfinding, not decoration.
- Anything mechanical here (fonttype 42, page budget, placeholder sweep) is enforced by `scripts/gate_artifact.sh` — don't re-litigate it visually.
