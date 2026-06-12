# Operator Guide

How to set up, launch, and live with a run of this harness. Every mechanism exists because of an observed failure mode of long-horizon autonomous research agents; the design-rationale map at the bottom traces each one. Read it once — it explains why the files look the way they do.

---

## 1. Pre-launch checklist

**Workspace.** Copy `workspace/` into the agent's OpenClaw workspace. Resolve every `{{PLACEHOLDER}}` (grep for `{{`): agent name, operator name, workspace path, accounts, GitHub remote, page budget, snapshot times, telemetry path, cloud-spend command, external reviewer access. Paste the task brief into `BRIEF.md` (the comment block lists what it must contain). `chmod +x workspace/scripts/*.sh`.

**Accounts — all verified working before launch, by you:**
- GitHub auth + an empty project remote.
- Telegram channel bound to the agent's main session.
- Email CLI authenticated.
- **External reviewer platforms** (Stanford agentic reviewer, CMU paper reviewer, refine.ink or equivalents): confirm login/submission works *now*. The final milestone is gated on external review, so a broken login discovered mid-run becomes a Tier-3 block.
- **Cloud quotas pre-approved, long-lived credentials.** Quota approvals can take longer than the run, and short-lived SSO tokens expire mid-run and silently kill scheduled jobs.
- **Resource caps and measurement.** State the caps (API $, cloud $, deadline) in `BRIEF.md`. Resolve `{{TELEMETRY_PATH}}` (the OpenClaw telemetry JSONL) and `{{CLOUD_SPEND_COMMAND}}` (a one-liner returning current cloud spend) in the workspace files, and verify `scripts/telemetry_costs.py` runs against the live telemetry file before launch. **Once, around hour 12:** compare the script's output against actual console billing; if they disagree materially, tell the agent the true number — it will record the correction factor in `TOOLS.md`. After that, the script is the agent's source of truth (it deduplicates by responseId; the threshold ladder in `AGENTS.md` acts on it).

**Intentionally absent files — do not recreate:** `SOUL.md`, `IDENTITY.md`, `SELF_KNOWLEDGE.md`, `BUDGET.md`, `SPRINT.md`. Usage audits of long runs find persona files ceremonial and self-knowledge files unconsulted after bootstrap — the load-bearing distillation belongs in `AGENTS.md`, which is consulted constantly. Sprint/crunch rules work better as permanent defaults than as an emergency mode. If the agent recreates one of these files, note it as a finding — don't intervene unless it's causing harm.

**openclaw.json:**

```json5
{
  agents: { defaults: {
    heartbeat: {
      every: "30m",          // the crunch block drops this to 10m via cron update
      skipWhenBusy: true,    // safe: self-chains carry the work; harvest-check catches drops
      target: "none"         // heartbeats update state; snapshots are separate fixed-time crons
    },
    subagents: { maxConcurrent: 5 }   // AGENTS.md self-limits to 3 in flight
    // no subagent nesting (default maxSpawnDepth) — the main agent orchestrates
  } }
}
```

**Launch:** send `PROMPT.md` as the first message. Expect within ~6h: a corrected `TOOLS.md`, a milestone schedule and candidate portfolio logged as the first Tier-2 memo, a first commit, and the first snapshot at the next scheduled time.

## 2. Living with the run (your side of the contract)

You will receive the scheduled snapshots — one-way status updates; nothing in them awaits your reply — plus, rarely, **two kinds of blocking message**: a Tier-3 environment failure the agent couldn't route around (fix the resource; reply when it's fixed), and the completion report. For anything deeper than the snapshots, watch passively: `git log`, `LOG.md`, and the state capsule in the daily memory file are the run's telemetry.

- **Don't message mid-run.** Every unsolicited instruction is an intervention: it can stall the agent, reshape its scope, or rescue it — all of which confound what the run measures. The agent treats your instruction as binding and logs it verbatim; if you must intervene, record it on your side too.
- **Decision authority is fully delegated.** Hard decisions happen internally: logged memos, critic-subagent review for contribution-changing calls, reversible defaults on branches. There is no consent window and nothing for you to approve. The log is the report.
- **The one intervention worth making:** confabulation. If you spot a number in prose that traces to no artifact, point at it — the agent must fix it everywhere, add a Lessons Learned entry, and register the number. If it recurs, lengthen milestones; never remove them.

## 3. The heartbeat, properly understood

Operational data from instrumented long runs overturns the intuition that heartbeat cadence drives productivity:

- **Idle is decision-gating, not cadence.** Hours lost to waiting on the operator dwarf hours lost to "the heartbeat didn't fire often enough" (which round to zero).
- **The engine of high-throughput stretches** is end-of-turn discipline: background job + pre-registered branch protocol + one-shot harvest cron. Heartbeats just sweep up.
- **Faster cadence mostly lands beats while subagents are in flight.** The higher-leverage spend is verification tied to events, not clocks — registered numbers are re-checked by the gate script at every review round and milestone gate.

Hence the design — **regime B: sweeper + task block** — 30m interval, `skipWhenBusy`, per-task intervals (harvest 30m / git 60m / milestone-clock 3h / resource-check 4h / blocker-recheck 6h), and a crunch drop to 10m inside the last 24h before a milestone. All forward drive comes from the End-of-Turn Contract in `AGENTS.md`; state-changing turns update the restart capsule themselves, and memos schedule their own deadline crons — clocks are safety nets, events do the work.

Alternatives, if you want to A/B across runs:
- **Regime A — fixed fast cadence (10m always):** maximum forward pressure; pairs well with ~30-min review/fix loops. Pays a per-beat token floor for beats that mostly land while subagents are in flight.
- **Regime C — pure event-driven (no heartbeat):** completion events + self-chains only. Cheapest; rejected as the default because cron/event routing is fragile in practice (see `TOOLS.md` § Footguns), and a missed event with no sweeper orphans a branch protocol indefinitely.

To compare regimes, log per run: fraction of beats that took a forward action; median time from job completion to harvest; count of orphaned branch protocols; $/day on heartbeats.

## 4. Variations register (what to vary across runs)

| Dimension | This template's default | Variation worth testing |
|---|---|---|
| Heartbeat regime | B (sweeper + tasks) | A (fixed 10m) / C (event-driven) — metrics above |
| Operator contact | One-way snapshots + two blocking triggers (environment failure, completion) | Fully silent (completion only); or memo-with-default windows (4–8h, silence = consent) for runs where mid-course human steering is itself under study |
| Milestone tightness | 3–6 milestones, agent-proposed | Operator-imposed schedule; or no deadlines (ablation — expect heavy idle) |
| Review caps | 2-clean-stop / hard cap 6 | 3 fixed rounds; external-first ordering |
| Bootstrap | None — principles fully pre-written | Directed reading (named papers → ≤4 task-specific principles) vs open-ended self-assessment |
| Pre-written principles | Shipped in AGENTS.md | Agent-derived from scratch (ablation of the pre-write) |
| Tooling additions | None (web search/fetch suffice for lit work) | Paper-search MCP; OpenClaw skills versions of the playbooks |
| Depth/rigor mechanisms | Not enforced (deliberately — each added rule is another thing to over-index on) | "Surprise protocol": conflicting results need a mechanistic explanation + discriminating experiment before entering the deliverable; evaluation-adequacy floors in the brief (min real datasets, non-degenerate headline table) |
| Project management | `PLAN.md` work queue + backlog (markdown, no dependencies) | Linear via MCP as a wholesale *replacement* for queue + backlog (never duplication). Measure: status-drift incidents caught, per-heartbeat overhead, operator-side visibility value. Outage fallback = revert to the `PLAN.md` table, never a Tier-3 stall |
| Subagent concurrency | ≤3 | ≤5 with tighter budgets |

## 5. Design rationale (failure mode → mechanism)

| Observed failure mode of long-horizon autonomous research agents | Mechanism in this harness |
|---|---|
| Escalations end in open-ended waits on the operator; idle can consume close to half the run once decision-independent work runs dry | Non-blocking contact protocol: snapshots are one-way broadcasts, and only environment failure or completion may ask anything of the operator (`USER.md`); hard decisions are internal logged memos with critic review, taken immediately on reversible branches (`playbooks/decisions.md`) |
| Throughput is highest when every turn ends with launched work, a pre-registered branch protocol, and a scheduled harvest — and collapses when turns end with a yield | End-of-Turn Contract (`AGENTS.md`); self-chain payload convention (`TOOLS.md`); harvest-check task as crash recovery |
| Deadline pressure produces the largest single behavioral improvement; without deadlines, pace drifts | Agent-authored milestone deadlines with gate scripts from hour 2 (`PLAN.md`); crunch block (`AGENTS.md`); deadlines gate dispatch, scripts gate truth |
| Endgame blind-review loops devolve: verdicts plateau within ~5 rounds, then contamination, streak-gaming, and zero soundness gain; external reviewers go unused without a gate | Stopping rule (2-clean / cap 6) + spawner-side contamination ban + review ledger + externals as a gated milestone (`playbooks/review.md`, `gate_artifact.sh`) |
| Hostile section reviews during drafting catch confabulated statistics within hours — the cheapest truth enforcement available | Kept as §1 of `playbooks/review.md` |
| Fabrication appears in both subagent reports (result tables absent from output files) and prose (statistics that reproduce from no artifact) | Evidence rules binding subagents (`AGENTS.md`), Evidence Blocks + parent spot-check (`playbooks/subagent.md`), audited-numbers registry verified at every review round and milestone gate |
| Frozen definitions drift between prose and code; prose cites locks that don't exist on disk | `locks/` pattern + registry + Tier-2 change policy |
| Drafting outside the target format defers page-budget and formatting failures to the final hours | Target-format-from-day-1 milestone gate; page-budget + placeholder gates in `gate_artifact.sh` |
| Agents iterate figures blind — code that runs without error gets treated as a good figure, and there's no spec to converge on | Figures playbook (`playbooks/figures.md`): spec-before-code, plot-from-cached-artifacts, mandatory render-and-look at final size, blind takeaway test by a context-free subagent; consolidated style canon so aesthetics aren't re-derived per figure |
| Cron/messaging routing fails silently; stale environment facts persist for days | Footgun ledger in `TOOLS.md` (subagents see it); hour-0 environment verification duty |
| Per-heartbeat journaling duplicates the decision log at ~90% noise | State capsule (10-line overwrite) updated at the end of any state-changing turn, spot-checked by harvest-check; analysis confined to `LOG.md` |
| A single living plan file drifts through repeated rewrites; append-only registries stay clean | Plan/registry split: rewriteable `PLAN.md` vs append-only `REGISTRY.md` |
| Under one-directional hostile review, headline claims shrink monotonically until the deliverable is over-pruned — every truth mechanism punishes overclaiming, nothing punishes hedging | Symmetric claim discipline: Claim ledger (`REGISTRY.md`) — no narrowing without a triggered falsifier or cost argument; underclaim audit (`playbooks/review.md` §1b); base + stretch claims in `PLAN.md`; significance floor in the `BRIEF.md` success bar |
| A polished deliverable can answer a different question than assigned: scope drift happens in locally-reasonable, honestly-evidenced steps, and PDF-only reviewers structurally cannot catch it — the brief's semantic constraints erode while every numeric constraint holds | Brief constraints registered with a checking mechanism each (`REGISTRY.md`); brief-fidelity review at every milestone gate and before the endgame, with DIVERGED forcing the pivot procedure (`playbooks/review.md` §3) |
| Single-candidate commitment makes reconsideration unaffordable: when the only idea dies, re-framing around surviving artifacts is cheaper than starting over, and mechanism-level failures get treated as code bugs until the only exit is re-scoping the question | Candidate portfolio with staged commitment — headline requires scouts from ≥2 candidates (`PLAN.md`); failure-level taxonomy (implementation / mechanism / question) in the stuck/pivot procedure (`playbooks/decisions.md`) |
| Budget tracking degenerates two ways: naive telemetry sums overcount severely until hand-corrected, and a ledger updated every heartbeat becomes informational ritual that never gates a decision; meanwhile agents start with no model of how long anything takes | Scripted measurement only (`scripts/telemetry_costs.py`, deduped; cloud via one command); numbers live in the overwritten state capsule, not a ledger; 70/90/100 threshold ladder wired to the decision tiers + pre-flight cost gate (`AGENTS.md` § Resources); predicted-vs-actual wall-clock logged at every harvest |
