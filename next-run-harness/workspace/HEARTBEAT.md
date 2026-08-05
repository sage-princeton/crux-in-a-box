# HEARTBEAT.md

**You are the sweeper, not the engine.** Forward drive comes from the End-of-Turn Contract in `AGENTS.md` (self-chains, subagent completions, memo defaults). If a self-chain or in-flight subagent already owns the next action, this beat does hygiene only — never duplicate or preempt in-flight work.

If nothing below needs attention, reply `HEARTBEAT_OK`.

**The intervals below are tuned for a multi-day horizon.** Derive the real cadence from the horizon at hour-0 (`AGENTS.md` § Horizon & cadence): on a sub-48h run, halve every interval here and set `milestone-clock` to ≈H/8, floor 10 min. Record the derived cadence in the state capsule.

tasks:
  - name: harvest-check
    interval: 30m
    prompt: "Any finished background job whose pre-registered LOG.md branch hasn't been executed and has no pending self-chain? Execute it now. Any subagent past +50% of its budget? Inspect, then preempt or re-scope. If the state capsule or the PLAN.md work queue doesn't match reality, fix it."
  - name: git-hygiene
    interval: 60m
    prompt: "git status — if the tree is dirty, commit with a descriptive message. The repo is local-only: do not attempt a push."
  - name: milestone-clock
    interval: 3h
    prompt: "Time-remaining to the next PLAN.md milestone? If the trajectory misses it, re-scope (Tier-2 memo if material) — never silently slip. If the next milestone is within ~10% of the horizon, activate the AGENTS.md crunch block (§ Horizon & cadence). In the Experiment phase, a *sufficient* experiment running with nothing to harvest and nothing to deepen is the MATURE state (AGENTS.md End-of-Turn Contract): reply HEARTBEAT_OK — do not invent churn to look busy. Conversely, if the deliverable has reached a ship-candidate state while >50% of the deadline AND >40% of budget remain (scripts/telemetry_costs.py + deadline; floors in locks/budget.json), that is an UNDER-SPEND signal, not a finish — the budget is a target to deploy on depth (AGENTS.md § Resources). Don't ship: deepen the strongest result the remaining budget can buy (more seeds/scale, the next portfolio candidate, the cheapest claim-strengthening experiment from playbooks/review.md §1b, or a backlog robustness/ablation), and log a one-line memo on what you're deploying it on. A costed-but-unrun strengthening experiment is forward motion you owe, NOT 'grinding' — it may not be declined via the Stuck tripwire (playbooks/decisions.md § Stuck/Pivot). If a cap really is (near-)exhausted, that's a genuine finish; record it honestly. One front-of-run note: don't draft prose before the exploration-sufficiency critic certifies the dossier adequate (playbooks/exploration.md §4)."
  - name: resource-check
    interval: 4h
    prompt: "Run scripts/telemetry_costs.py (API spend) and the TOOLS.md cloud-spend command; update the capsule line as BURN-RATE vs RUNWAY (API $X/cap · cloud $Y/cap · deadline T-minus · projected end-state spend at current burn). Surface under-spend continuously, not just at ship: if the burn trajectory lands far under cap with time to spare, that is the felt-budget signal (AGENTS.md § Resources) — flag it now and deploy the slack on depth, don't wait for the milestone-clock to catch it. Near a cap → consolidate (AGENTS.md § Resources); a breach is Tier-3."
  - name: blocker-recheck
    interval: 6h
    prompt: "Any open Tier-3 environment blocker? Re-test the resource; if recovered, log it and resume the blocked work. Any Tier-2 memo branch still waiting on its named confirmation/reversal evidence? Check it — merge or revert."

## Scheduled snapshots

Two Telegram snapshots per day at {{SNAPSHOT_TIMES|10:00 and 19:00}}, sent by separate crons targeting the main session. **On a horizon under 48h, fixed clock times don't fit** — send snapshots at ~33% and ~66% of the horizon instead (`AGENTS.md` § Horizon & cadence), so the operator still gets two, positioned meaningfully (`TOOLS.md` § Footguns). Content and the no-questions rule: `USER.md` § Status updates. Snapshots replace all ad-hoc pings.
