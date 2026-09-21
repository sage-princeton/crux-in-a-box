---
name: figure-resource-timeline
description: >-
  Build the resource-timeline figure for one CRUX run — wall-clock time and API
  spend as a share of the run's allotment, against hours after start, with
  numbered phase annotations — from the run's host timeline (crux-harness
  timeline.jsonl) or an OpenClaw run_events.jsonl export. Use when writing up a
  finished run, comparing runs, or checking how a run spent its budget.
---

# Resource timeline figure

Produces the **resource-timeline figure**: the two-line chart (wall-clock %,
API spend %, plus any extra metered series) with numbered phase chips and the
phase notes under it, in the CRUX 2 figure style. Two steps: a deterministic
collector that reads the run record, and a builder that renders the data plus
your phase annotations.

Paths below: `$SKILLS` is this skills directory (`analysis/skills` in
crux-in-a-box, `.claude/skills` inside an exported run repo); `$RUN` is the
run repo root (`runs-export/<run>/repo` in crux-in-a-box, `.` inside the
export); `$OUT` is an output directory of your choice (in crux-in-a-box use
`analysis/clean-room/<run>/`, which is gitignored).

## Inputs you need

1. **The run's host timeline** — `crux-harness` runs: the
   `<run>.timeline.jsonl` (or `.jsonl.gz`) the harness writes on the host,
   exported as `$RUN/host/<run>.timeline.jsonl.gz` in the run repo or under
   `timeline/` in a `crux-collect` pull. It carries the allotments
   (`run.context`), every metered call (`model.usage`), and the operator
   interventions, final pass and stop reason. OpenClaw runs: the scrubbed
   `run_events.jsonl` from `utils/export-run.sh`, plus the allotments by hand
   (`--hours`, `--budget`), which that export does not record.
2. **The run's `LOG.md`** (optional but recommended): the collector emits every
   entry header with its hour so phase boundaries can be anchored to the log.
3. Optional **extra metered series** — an experiment-provider or GPU ledger as
   a `(time, cost)` CSV of increments with a cap, e.g. the agent's own
   `artifacts/.../experiment_costs` ledger or a provider export.
4. **Phase sections** you author (`sections.json`, from
   `sections.template.json`): 5–8 phases with a headline label and a
   plain-language tip, each boundary anchored to a log entry, commit, or
   harness event.

## Steps

1. Collect:

   ```
   python3 $SKILLS/figure-resource-timeline/collect.py \
     --timeline $RUN/host/<run>.timeline.jsonl.gz \
     --log $RUN/LOG.md \
     [--extra "Experiment provider=<costs.csv>:1000"] \
     --out $OUT/timeline.json
   ```

   Read the emitted `events` (interventions, final pass, final gate, stop) and
   `log_entries` (hour + title) to choose phase boundaries.

2. Author `$OUT/sections.json` from `sections.template.json`. Record each
   boundary's evidence in `basis`.

3. Build:

   ```
   python3 $SKILLS/figure-resource-timeline/build.py \
     --data $OUT/timeline.json \
     --sections $OUT/sections.json \
     --label "GPT-6 Astra · Codex CLI" \
     --out $OUT/resource-timeline.png
   ```

   The title is generated from the data ("In the … run, the agent used N% of
   the available API budget — T tokens — over H hours"); override it with
   `--title` only for wording, never to change a number.

## Judgment rules

- The x-axis origin is the run's **first model call**, not the launch
  message or the container start.
- The wall-clock line is drawn against the run's allotted window
  (`run_hours`), so the x-axis runs to the full allotment even when the run
  ended early.
- Spend is what the host metered on the bridge (`cum_cost_usd`): the session,
  heartbeats, subagents and the isolated reviewer together. The agent's own
  cost display inside the container prices against a table that does not
  apply; never substitute it.
- Never hand-edit series values or totals. If a number looks wrong, fix the
  input and re-run the collector.
- Phase labels and tips are editorial, but every boundary must rest on a
  timestamp you can point to. Tips describe what happened, not what the
  reader should conclude.

## Verify

`python3 $SKILLS/figure-resource-timeline/test.py` — runs the collector on
synthetic harness and OpenClaw fixtures and checks the series and totals;
renders the fixture figure if Chrome is available; and, when the codex-astra
run data is present (under `runs-export/` or as the repo itself), reproduces
that run's endpoint totals from its real timeline.

Dependencies: Python 3 (stdlib), Google Chrome for rendering (`CHROME=...` to
override the path), network at build time for the Hanken Grotesk web font.
