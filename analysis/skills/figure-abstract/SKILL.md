---
name: figure-abstract
description: >-
  Build the abstract screen-grab figure — page one of the agent's submitted
  paper (title, authors, abstract) as a framed, captioned panel in the CRUX 2
  composite style — from the run's paper.pdf. Use when a write-up needs to
  show what the agent actually submitted.
---

# Abstract screen-grab figure

Produces the **abstract figure**: the first page of the paper the run
delivered, rasterised and set in the CRUX 2 composite frame with a caption
naming the author model, the paper title and the page count. One paper per
figure.

Paths below: `$SKILLS` is this skills directory (`analysis/skills` in
crux-in-a-box, `.claude/skills` inside an exported run repo); `$RUN` is the
run repo root (`runs-export/<run>/repo` in crux-in-a-box, `.` inside the
export); `$OUT` is an output directory of your choice (in crux-in-a-box use
`analysis/clean-room/<run>/`, which is gitignored).

## Inputs you need

1. **The paper PDF** the run delivered: `$RUN/paper/paper.pdf` (or the
   quick-look copy in `runs-export/<run>/FINAL/`). Use the final compiled PDF,
   not an intermediate build product.
2. Optionally the `.tex` beside it — the caption's title is read from
   `\title{...}` when present (else from the page text via `pdftotext`, else
   pass `--paper-title`).

## Steps

```
python3 $SKILLS/figure-abstract/build.py \
  --pdf $RUN/paper/paper.pdf \
  --label "GPT-6 Astra · Codex CLI" \
  --crop 0.6 \
  --out $OUT/abstract.png
```

- `--crop 0.6` keeps the top 60% of the page (title through the abstract);
  omit it for the whole page.
- `--heading`, `--subtitle` and `--caption` replace the generated text when the
  write-up needs editorial framing; the caption's quoted title and page count
  are facts, keep them.

## Judgment rules

- Show the page as compiled inside the run; never re-typeset, re-crop to
  hide, or annotate the page image itself.
- The caption's author label names the model and CLI that wrote the paper,
  the way the other figures do ("GPT-6 Astra · Codex CLI").

## Verify

`python3 $SKILLS/figure-abstract/test.py` — writes a one-page fixture PDF,
renders it (needs `pdftoppm` or `qlmanage`, and Chrome), and checks the
caption picked up the fixture's title and page count; then renders the
codex-astra paper when that run data is present.

Dependencies: poppler (`pdftoppm`, `pdfinfo`, `pdftotext`; `brew install
poppler`) or macOS `qlmanage` for page 1 only; Google Chrome.
