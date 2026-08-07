---
name: collect-milestones
description: Collect planned-vs-actual milestone timing (and API spend at each milestone) for a CRUX run's milestone dumbbell figure, parsing the run workspace's PLAN.md. Use when building or refreshing a crux2-*-milestones chart for a new run.
---

# Collect milestone data

Produces the data behind the **milestone-timing figure** (planned deadline vs
actual completion per milestone, as hours after launch) and its API-spend
variant (dollars consumed by each time).

## Inputs you need

1. The run workspace's **PLAN.md** — planned deadlines are parsed from the
   milestone table (the row table with a "Deadline (absolute)" column). The
   agent writes this table within ~2h of launch; take the ORIGINAL deadlines,
   not later retargets (the parser takes the first timestamp per cell).
2. An **actuals JSON** you author:
   `{"launch": ISO-UTC, "milestones": [{"num": 1, "label": "...", "actual": ISO-UTC}, ...]}`
   - `launch` = the run's first API-usage minute.
   - Each `actual` is a judgment call **cross-validated against LOG.md**
     (PLAN.md status cells go stale). Convention: "actual" = the milestone's
     own gate/deliverable passing (gate-green, critic-ADEQUATE, review
     convergence); the completion milestone = the FIRST below-bar completion
     report, before any extension.
3. Optional: the per-minute Anthropic cost CSV, to also emit spend at each
   planned/actual time.

## Steps

```
python3 .claude/skills/collect-milestones/collect.py \
  --plan <run>/PLAN.md --actuals actuals.json [--cost-csv cost.csv]
```

Feed `planned_hours`/`actual_hours` into the `*-milestones-time` dumbbell and
`spend_at_*` into the `*-milestones-api` dumbbell in `app/data/plots.ts`.
Record the actual timestamps and their LOG.md line references as a comment
next to the data (see the existing plots.ts comments) so the judgment is
auditable.

## Verify

`python3 .claude/skills/collect-milestones/test.py` — parses both CRUX-2 run
repos' PLAN.md files live and reproduces every published dumbbell value
(hours to ±0.06h, spend to ±$1).
