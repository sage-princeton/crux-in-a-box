# HEARTBEAT.md

A heartbeat is: recover your bearings, then act. You may be arriving with thin or compacted context — re-orient before doing anything:

1. The task, requirements, and budgets are at the top of `AGENTS.md` (in context).
2. `PLAN.md` — current plan, resource ledger, work in flight.
3. The tail of `LOG.md` — the latest decisions.
4. Live state — running jobs (`runs/*/pid`), in-flight subagents, `cron list`, any GPU pods.

Then, in priority order:

- **Harvest** any finished subagent or background job: read its report/output, log the result, act on it. Any work-in-flight row overrunning — a subagent past +50% of its budget, a background job whose `out.log` has gone silent, a GPU pod billing with no results arriving — gets inspected now: preempt and re-scope if wedged, correct the row if the estimate was wrong. Never just keep waiting.
- **Refresh the ledger** if its numbers are more than ~3 hours old: `python3 scripts/telemetry_costs.py`, the RunPod balance, the clock against the deadline — update `PLAN.md` § Current position. If the trajectory misses a milestone or a cap, revise the plan now, not later.
- **Commit and push** if the tree is dirty.
- **Take the next action** from `PLAN.md`.

If real work is already in flight and nothing above needs doing, reply `HEARTBEAT_OK`.
