# crux-in-a-box analysis

This directory is home to tools that are useful for analyzing CRUX runs that
are in progress or completed.

## clean-room

This is a git ignored directory that can be used to save and analyze data.
These data can be copied from CRUX runs, and/or pulled from other sources.
We recommend instructing coding agents to use this as a clean room, where
information can be saved and analyzed.

## skills

This is a collection of useful skills that we have compiled for agents to aid
in our analysis of CRUX runs. Each is a directory with a `SKILL.md` (what it
produces, the inputs it needs, the steps, the judgment rules, how to verify)
and the scripts it drives.

| skill | produces | from |
| --- | --- | --- |
| `crux-run-timeline/` | a self-contained `timeline.html`: plain-language story, 5-Whys root cause, telemetry activity panel, searchable event log | a live or finished OpenClaw box over SSH |
| `figure-resource-timeline/` | the resource-timeline figure: wall-clock % and API spend % against hours after start, numbered phase chips and notes | the run's host timeline (`crux-harness` `timeline.jsonl`) or an OpenClaw `run_events.jsonl`, plus phase sections you author |
| `figure-referee-rounds/` | the referee-rounds figure: every blind review round as a row of facet scores with its Overall verdict | `reviews/blind_round_*.md`, timed from git, a git-log rendering, or `LOG.md` |
| `figure-abstract/` | the abstract screen grab: page one of the submitted paper, framed and captioned | `paper/paper.pdf` |
| `figure-milestones/` | the milestone dumbbells: planned deadline vs actual completion per milestone, and API spend at each | the `PLAN.md` milestone table (from the right git revision) and the host timeline |

The four `figure-*` skills are two-step — a stdlib-only collector that turns
run artifacts into a small JSON, then a builder that renders it — and share
`_lib/figstyle.py`: the CRUX 2 figure look (palette, geometry, Hanken Grotesk)
and the renderer, which measures the page with a headless-Chrome `--dump-dom`
pass and screenshots it at exactly that size at 2x. Dependencies: Python 3,
Google Chrome (`CHROME=...` to override), poppler for the abstract figure.

`sh analysis/skills/run-figure-tests.sh` runs every figure skill's tests:
fixtures always, the real `runs-export/` data and Chrome rendering when present.

The figure skills are written to work unchanged from inside an exported run
repo too: copy `_lib/`, the `figure-*/` directories and
`run-figure-tests.sh` into that repo's `.claude/skills/`, and the `$RUN`
paths in each `SKILL.md` become `.`.
