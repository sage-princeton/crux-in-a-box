# PLAN.md — Living Plan

_Rewriteable. This file describes intent and schedule; the append-only record of what happened and why is `LOG.md`; frozen pre-registrations live in `REGISTRY.md` and `locks/`. When this file and the registry disagree, the registry wins._

## Goal

_One paragraph: what the brief asks for, restated in your own words. (Write during hour 0–2.)_

## Current approach

_The method/hypothesis currently being pursued, with pointers to its `REGISTRY.md` falsifiers. Rewrite freely as direction changes; log each change in `LOG.md`._

**Headline claim (base):** _the claim the deliverable will make, locked at plan time and tracked in `REGISTRY.md` § Claim ledger._
**Headline claim (stretch):** _the stronger form, with its falsifier registered. Settling for the base without having run the stretch falsifier requires a logged reason (usually cost). Don't abandon a claim that hasn't been falsified; don't keep one that has._

## Candidate portfolio

_Filled during planning, before any headline commitment: 2–3 ranked approach candidates spanning the design space the brief points at. **Committing to a headline approach requires scout results from at least two.** When the leading candidate fails at the mechanism level (`playbooks/decisions.md` § Failure levels), the default next action is advancing the next candidate; re-scoping to a different kind of contribution instead is a Tier-2 memo. A candidate is cheap to keep alive (one table row) and expensive to resurrect from nothing — keep the losers' rows current. **A candidate is only marked killed or promoted by a scout that returns a clean FAIL/PASS against its pre-registered kill/promotion criterion (verdict bins, `AGENTS.md` § Pre-registration); an AMBIGUOUS, MALFORMED, or single-seed / under-powered scout does not settle it — deepen or re-run before narrowing the headline. The portfolio is the `## HYPOTHESIS PORTFOLIO` of the Exploration Dossier (`playbooks/exploration.md`); committing to a headline — and entering drafting — requires not just ≥2 scouts but the exploration-sufficiency critic's ADEQUATE verdict. Exhausting the portfolio with only one viable direction (the others settled by clean FAIL) is honest convergence, not a stall.**_

| rank | candidate (mechanism premise) | cheapest scout | kill criterion | promotion criterion | status |
|---|---|---|---|---|---|

## Milestone table

_Write within ~2 hours of reading `BRIEF.md`; log as the first Tier-2 memo. Rules:_
- _3–6 milestones, each with an absolute deadline and a gate command that must pass; every gate includes the brief-fidelity review (`playbooks/review.md` §3)._
- _Milestone 1: deliverable skeleton compiling in the final target format (an empty shell built from the venue template `templates/paper_template.zip`), page-budget gate live, plan/falsifiers registered. The shell does **not** authorize prose — its gate is the plain skeleton-mode `gate_artifact.sh`. Exploration-adequacy and headline-substance gate **drafting**, not the shell (keeping the skeleton early preserves the target-format-from-day-1 anti-failure mechanism)._
- _Exploration precedes drafting: a gated **Exploration Dossier** milestone (`playbooks/exploration.md`) — lit synthesis + ≥2 candidates scouted to clean verdicts + the chosen direction endorsed by the isolated sufficiency critic — comes before any drafting milestone._
- _Drafting milestones follow `playbooks/writing.md` — compressed narrative first; no full prose while a headline number lacks its `REGISTRY.md` row. Stage-1 prose may not begin until the drafting-entry gate passes: `REQUIRE_EXPLORATION_ADEQUATE=1 REQUIRE_HEADLINE_SUBSTANCE=1 scripts/gate_artifact.sh <pdf>`._

| # | Milestone | Deadline (absolute) | Gate (command that must pass) | Status |
|---|---|---|---|---|
| 1 | Env verified + deliverable skeleton compiles in target format (empty shell) + page-budget gate live + plan/falsifiers registered | | `scripts/gate_artifact.sh <pdf>` (skeleton mode) | |
| 2 | Exploration Dossier: lit synthesis + ≥2 candidates scouted to clean verdicts + direction critic-endorsed (`playbooks/exploration.md`) | | `REQUIRE_EXPLORATION_ADEQUATE=1 scripts/gate_artifact.sh <pdf>` | |
| 3 | Headline locked + first full draft begins (drafting entry) | | `REQUIRE_EXPLORATION_ADEQUATE=1 REQUIRE_HEADLINE_SUBSTANCE=1 scripts/gate_artifact.sh <pdf>` | |
| … | Final: externals reviewed + camera-ready + accompanying final review | | `REQUIRE_EXTERNAL_REVIEWS=1 scripts/gate_artifact.sh <pdf>` | |

## Work queue (current milestone)

_The middle altitude between the milestone table and the in-flight capsule: every real piece of work toward the current milestone gets a row the moment it's conceived — no mental notes. One row per coherent task: don't fragment one task across five rows or bury five tasks in one. Rows are maintained by the turns that touch them — a LAUNCH/DELEGATE turn opens or advances its row, a harvest closes it — and the harvest-check heartbeat task catches drift. If the queue is empty but the milestone gate doesn't pass, that's a planning error: fix the queue, not the gate._

| task | owner (main / subagent / process) | budget | status | blocked on |
|---|---|---|---|---|

## Decision-independent backlog

_Standing list of useful work requiring no pending decision and no operator input. Pull from here during every Tier-2 memo window and Tier-3 block. Keep ≥3 items at all times; when it runs dry, generating more backlog IS the next task._

- Reproducibility hardening (one-command repro of current headline numbers)
- Robustness/ablation depth on already-locked claims
- Lit-gap closure (verify any citation still marked unverified)
- Documentation/readme debt
