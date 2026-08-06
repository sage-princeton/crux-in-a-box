# HEARTBEAT.md

**You are the sweeper, not the engine.** Forward drive comes from the End-of-Turn Contract in `AGENTS.md` (self-chains, subagent completions, memo defaults). If a self-chain or in-flight subagent already owns the next action, this beat does hygiene only — never duplicate or preempt in-flight work.

If nothing below needs attention, reply `HEARTBEAT_OK`.

**Cadence scales with the horizon.** The intervals below are calibrated to a multi-day run. On a short horizon (`BRIEF.md` § Budgets — e.g. a 1-day run) they are too slow to be a useful safety net: divide each by the ratio of a week to the actual horizon, floor 5 min for the sweeper and 30 min for the slowest task, and set them at hour 0 with `cron update`. What must not change is the *ordering* — sweeper fastest, blocker-recheck slowest.

tasks:
  - name: harvest-check
    interval: 30m
    prompt: "Any finished background job whose pre-registered LOG.md branch hasn't been executed and has no pending self-chain? Execute it now. Any subagent past +50% of its budget? Inspect, then preempt or re-scope. If the state capsule or the PLAN.md work queue doesn't match reality, fix it."
  - name: git-hygiene
    interval: 60m
    prompt: "git status — if the tree is dirty, commit with a descriptive message. The repo is local only; there is nothing to push."
  - name: milestone-clock
    interval: 3h
    prompt: "Time-remaining to the next PLAN.md milestone, as a FRACTION of the run horizon (locks/budget.json launch_iso→deadline_iso), never as a raw hour count? If the trajectory misses it, re-scope (Tier-2 memo if material) — never silently slip. If under {{CRUNCH_FRACTION|10}}% of the horizon remains to it, activate the AGENTS.md crunch block. In the Experiment phase, a *sufficient* experiment running with nothing to harvest and nothing to deepen is the MATURE state (AGENTS.md End-of-Turn Contract): reply HEARTBEAT_OK — do not invent churn to look busy. Conversely, if the deliverable has reached a ship-candidate state while >50% of the deadline AND >40% of budget remain (scripts/telemetry_costs.py + deadline; floors in locks/budget.json), that is an UNDER-SPEND signal, not a finish — the budget is a target to deploy on depth (AGENTS.md § Resources). Don't ship: deepen the strongest result the remaining budget can buy (more seeds/scale, the next portfolio candidate, the cheapest claim-strengthening experiment from playbooks/review.md §1b, or a backlog robustness/ablation), and log a one-line memo on what you're deploying it on. A costed-but-unrun strengthening experiment is forward motion you owe, NOT 'grinding' — it may not be declined via the Stuck tripwire (playbooks/decisions.md § Stuck/Pivot). If a cap really is (near-)exhausted, that's a genuine finish; record it honestly. One front-of-run note: don't draft prose before the exploration-sufficiency critic certifies the dossier adequate (playbooks/exploration.md §4)."
  - name: resource-check
    interval: 4h
    prompt: "Run scripts/telemetry_costs.py (API spend) and the TOOLS.md cloud-spend command; update the capsule line as BURN-RATE vs RUNWAY (API $X/cap · cloud $Y/cap · deadline T-minus · projected end-state spend at current burn). Surface under-spend continuously, not just at ship: if the burn trajectory lands far under cap with time to spare, that is the felt-budget signal (AGENTS.md § Resources) — flag it now and deploy the slack on depth, don't wait for the milestone-clock to catch it. Near a cap → consolidate (AGENTS.md § Resources); a breach is Tier-3."
  - name: blocker-recheck
    interval: 6h
    prompt: "Any open Tier-3 environment blocker? Re-test the resource; if recovered, log it and resume the blocked work. Any Tier-2 memo branch still waiting on its named confirmation/reversal evidence? Check it — merge or revert."

## Scheduled snapshots

Telegram snapshots on a fixed cadence of **{{SNAPSHOT_CADENCE|every 6 hours}}**, sent by a cron targeting the main session (`TOOLS.md` § Footguns). The cadence is horizon-relative, not clock-time — set it at hour 0 so the run produces roughly 4–8 snapshots end to end whatever its length, and so a short run isn't silent. Content and the no-questions rule: `USER.md` § Status updates. Snapshots replace all ad-hoc pings.
