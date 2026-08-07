---
name: collect-resource-curves
description: Collect the wall-clock / API-spend / GPU-compute series for a CRUX run's resource-use figure from its cost telemetry, and validate the run-section boundaries. Use when building or refreshing a crux2-*-resources chart for a new run.
---

# Collect resource curves

Produces the data behind the **resource-use figure** (the three-line chart:
wall-clock %, Anthropic API spend %, GPU compute %, with numbered event
annotations) for one CRUX run.

## Inputs you need

1. **Per-minute Anthropic cost CSV** (`time,tokens,cost`) for the run's API
   key — export it with `visualizations/fetch_anthropic_usage.py` (needs the
   Anthropic admin key; one CSV per key, minute granularity).
   To identify which key belongs to which run: the key's traffic must start
   at the run's first recorded minute, and an early in-log spend note (e.g.
   "$33.82 spent so far") must line up with the key's cumulative bill.
2. **Hourly RunPod CSV** (`time_utc,gpuId,amount_usd,...,in_run_window`) —
   only rows with `in_run_window == 1` count.
3. **Budgets**: the run's API budget and GPU cap (from the run's BRIEF.md;
   CRUX 2 used $3,000 API for both runs, $100/$500 GPU caps).
4. **Original deadline** and **actual end** timestamps (UTC). The wall-clock
   series shows time as a share of the _currently allotted_ window, so an
   extension makes it hit 100%, drop, and climb back to 100%.
5. Optional: a `*_run_sections.json` (title/start/end per section). Sections
   are authored by reading the run's LOG.md — each boundary must be the exact
   minute of a journal entry or supervisor message, and sections must tile
   the run perfectly (the script validates this).

## Steps

1. Run the collector (stdlib-only, no deps):

   ```
   python3 .claude/skills/collect-resource-curves/collect.py \
     --cost-csv <cost.csv> --runpod-csv <runpod.csv> \
     --api-budget 3000 --gpu-cap <cap> \
     --deadline <ISO-UTC> --end <ISO-UTC> \
     --sections <run_sections.json>
   ```

2. Paste/diff the emitted `series` into the run's `LinesData` entry in
   `app/data/plots.ts`; use `sections` for the `xMarkers`/annotation x-values.
3. The numbered event annotations themselves (the 6 callouts under the chart)
   are **editorial**: pick ~6 pivotal LOG.md events, cross-validate each
   timestamp against the log, and convert to hours after the run's first API
   minute.

## Judgment rules

- The x-axis origin is the run's **first API-usage minute**, not the kickoff
  message.
- Never hand-edit series values; if a number looks wrong, fix the inputs and
  re-run.

## Verify

`python3 .claude/skills/collect-resource-curves/test.py` — reproduces the
published CRUX-2 endpoints and spot values for both runs from the checked-in
CSVs in `visualizations/crux-2-data/`.
