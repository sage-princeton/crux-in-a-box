# HEARTBEAT.md

A heartbeat is: recover your bearings, then act. You may be arriving with thin or compacted context — re-orient before doing anything:

1. The task, requirements, and budgets are at the top of `AGENTS.md` (in context).
2. `PLAN.md` — current plan, resource ledger, work in flight.
3. The tail of `LOG.md` — the latest decisions.
4. Live state — running jobs (`runs/*/pid`), in-flight subagents, `cron list`, any GPU pods.

A beat that finds nothing changed should cost almost nothing: if the work-in-flight table matches reality, nothing has finished, the ledger is fresh, and the tree is clean, reply HEARTBEAT_OK and stop — don't re-read LOG.md or re-derive the plan; nothing has changed since a turn that already did. Waiting on a sufficient experiment is forward motion — never invent small work to look busy. Stepping back is not this beat's job: the ledger beat (AGENTS.md § Your budget) fires on its own cron every {{LEDGER_BEAT_HOURS|6}} hours and owns reflection.

The one thing to police: if PLAN.md § Current position is older than about 1.5× the ledger-beat cadence, the ledger-beat cron is dead or was never created — do its work now (refresh, step back) and recreate the cron before yielding.

Then, in priority order:

- **Harvest** any finished subagent or background job: read its report/output, log the result, act on it. Any work-in-flight row overrunning — a subagent past +50% of its budget, a background job whose `out.log` has gone silent, a GPU pod billing with no results arriving — gets inspected now: preempt and re-scope if wedged, correct the row if the estimate was wrong. Never just keep waiting.
- **Commit** (locally) if the tree is dirty.
- **Take the next action** from `PLAN.md`.

If real work is already in flight and nothing above needs doing, reply `HEARTBEAT_OK`.
