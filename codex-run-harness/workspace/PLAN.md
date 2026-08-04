# PLAN.md — the resource plan

The allocation of this run's three budgets — clock, API spend, GPU spend — across phases. Written by the agent in the first iteration (hour-0 duty, `AGENTS.md` § Resources), reconciled every iteration against `scripts/budget_status.sh`, and revised whenever reality diverges (each revision logged in `LOG.md` with a reason). A fresh context inherits intent from this file — keep it current enough to be inherited.

Use absolute UTC checkpoint times (the deadline and polish window come from `scripts/budget_status.sh`). Size the allocations so the **full budgets are deployed by the deadline**, with experiments complete before the final-polish window.

| Phase | Checkpoint (UTC) | Hours | API $ | GPU $ | Status |
|---|---|---|---|---|---|
| (e.g. Lit deep-read + approach scouting) | | | | | not started |
| (e.g. Main experiments) | | | | | not started |
| (e.g. Robustness, baselines, ablations) | | | | | not started |
| (e.g. Writing + review rounds) | | | | | not started |
| (Presentation overhaul + README — the reserved polish window) | | | | | not started |

## Reconciliation (update each iteration)

- Last reconciled: (UTC time, iteration N) — on plan / revised (see LOG.md entry)

## Revisions

- (UTC date — what changed and why, one line each)
