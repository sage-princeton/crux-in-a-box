---
name: figure-milestones
description: >-
  Build the milestone dumbbell figure for a CRUX run — each planned milestone
  deadline against its actual completion, as hours after launch, and the API
  spend consumed at each of those moments — from the run workspace's PLAN.md
  milestone table and the host timeline. Use when writing up how a run paced
  itself against its own schedule and budget.
---

# Milestones figure

Produces the **milestone figure**: a dumbbell per milestone — hollow marker
at the planned deadline, filled marker at the actual completion, the bar
between them coloured by whether it came early or late — on an hours-after-
launch axis, with a second panel showing API spend at those same moments.
Two steps: a deterministic collector over the plan and timeline, and a
builder.

Paths below: `$SKILLS` is this skills directory (`analysis/skills` in
crux-in-a-box, `.claude/skills` inside an exported run repo); `$RUN` is the
run repo root (`runs-export/<run>/repo` in crux-in-a-box, `.` inside the
export); `$OUT` is an output directory of your choice (in crux-in-a-box use
`analysis/clean-room/<run>/`, which is gitignored).

## Inputs you need

1. **The run workspace's PLAN.md** — the milestone table (a row table with
   `#`, `Milestone`, `Deadline`, `Status` columns; the harness seeds it and
   the agent fills it within its first hours). Agents rewrite the plan: read
   the revision that still holds the ORIGINAL deadlines, not later retargets.
   In a run repo: `git log --format='%h %cI' -- PLAN.md`, then pick the last
   revision whose table still has the first-set deadlines and pass it as
   `--plan-rev`. The parser takes the first timestamp in each Deadline cell.
2. **Actual completion times.** The collector reads a timestamp from the
   Status cell when there is one ("Completed 2026-09-03 19:27 UTC"). For the
   rest, author `actuals.json` from `actuals.template.json`: "actual" is the
   milestone's own gate/deliverable passing (gate green, blind review returned,
   completion report written), cross-validated against LOG.md, with the
   evidence in `note`. The completion milestone is the FIRST completion
   report, before any extension.
3. **The run timeline** (`$RUN/host/<run>.timeline.jsonl.gz`) for the launch
   time and the cumulative spend; or `--start <ISO>` without spend.

## Steps

```
python3 $SKILLS/figure-milestones/collect.py \
  --plan $RUN/PLAN.md --plan-rev <rev> \
  --timeline $RUN/host/<run>.timeline.jsonl.gz \
  --actuals $OUT/actuals.json \
  --out $OUT/milestones.json

python3 $SKILLS/figure-milestones/build.py \
  --data $OUT/milestones.json \
  --label "GPT-6 Astra · Codex CLI" \
  --out $OUT/milestones.png
```

`--no-spend` drops the second panel. Milestones without an actual time draw
as a planned marker with "not completed".

## Judgment rules

- Planned deadlines are what the agent first scheduled; a retarget is a
  fact for the prose, not a new planned value.
- Actual times are judgment calls and must cite evidence; keep the
  `note` field filled and keep `actual_source` visible in the JSON.
- Hours are after the run's first model call; spend is the host-metered
  cumulative cost at that instant (never the agent's in-container estimate).

## Verify

`python3 $SKILLS/figure-milestones/test.py` — parses a fixture plan (one
Status-cell time, one actuals-JSON override, one incomplete milestone)
against a fixture timeline and checks hours and spend; renders the fixture
figure if Chrome is available; and, when the codex-astra run data is present,
reads that run's original table from its PLAN.md history.
