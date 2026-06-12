# HEARTBEAT.md

**You are the sweeper, not the engine.** Forward drive comes from the End-of-Turn Contract in `AGENTS.md` (self-chains, subagent completions, memo defaults). If a self-chain or in-flight subagent already owns the next action, this beat does hygiene only — never duplicate or preempt in-flight work.

If nothing below needs attention, reply `HEARTBEAT_OK`.

tasks:
  - name: harvest-check
    interval: 30m
    prompt: "Any finished background job whose pre-registered LOG.md branch hasn't been executed and has no pending self-chain? Execute it now. Any subagent past +50% of its budget? Inspect, then preempt or re-scope. If the state capsule or the PLAN.md work queue doesn't match reality, fix it."
  - name: git-hygiene
    interval: 60m
    prompt: "git status — if the tree is dirty, commit with a descriptive message and push."
  - name: milestone-clock
    interval: 3h
    prompt: "Time-remaining to the next PLAN.md milestone? If the trajectory misses it, re-scope (Tier-2 memo if material) — never silently slip. If under 24h, activate the AGENTS.md crunch block."
  - name: resource-check
    interval: 4h
    prompt: "Run scripts/telemetry_costs.py (API spend) and the TOOLS.md cloud-spend command; update the capsule line (API $X/cap · cloud $Y/cap · deadline T-minus). Crossing 70% or 90% of any cap triggers the AGENTS.md threshold ladder."
  - name: blocker-recheck
    interval: 6h
    prompt: "Any open Tier-3 environment blocker? Re-test the resource; if recovered, log it and resume the blocked work. Any Tier-2 memo branch still waiting on its named confirmation/reversal evidence? Check it — merge or revert."

## Scheduled snapshots

Two fixed-time Telegram snapshots daily at {{SNAPSHOT_TIMES|10:00 and 19:00}}, sent by separate crons targeting the main session (`TOOLS.md` § Footguns). Content and the no-questions rule: `USER.md` § Status updates. Snapshots replace all ad-hoc pings.
