# PLAN.md — Living Plan

_Rewriteable. This file describes intent and schedule; the append-only record of what happened and why is `LOG.md`; frozen pre-registrations (the falsifiers, the locks index, the claim trail) live in `REGISTRY.md` and `locks/`. When this file and a frozen falsifier or lock disagree, the frozen one wins._

## Goal

_One paragraph: what the brief asks for, restated in your own words. (Write during hour 0–2.)_

## Research plan (the spine)

_The intellectual center of the run — a **living, two-pass** artifact, not an hour-0 commitment. **Seed it hour 0–2** with only what is honest before reading the literature: the question restated, and provisional hypotheses/directions to *aim* the lit review (the budget capacity is costed in § Budget-deployment menu). The lit-dependent parts below — the calibrated *strong-vs-weak* target, the single most decisive experiment, the real cruxes, and the committed direction — are the **output of the Exploration Dossier** (`playbooks/exploration.md`) and get filled in / revised there, gated by the exploration-sufficiency critic; writing them as hour-0 guesses is the premature-commitment trap exploration exists to avoid. Revisit the spine at every milestone. This is the north star for **quality**, not just defensibility. Five parts:_

- **The question.** _The brief's research question in your own words, sharpened to what you can actually decide with this budget._
- **Hypotheses (1–3) and how they relate.** _The mechanism premises you'll test, and whether they're rivals (only one can be the headline), complements (they compose), or a base/stretch pair. Each gets a pre-registered falsifier in `REGISTRY.md` § Falsifiers before its first experiment._
- **What a *strong* result looks like vs. a weak one — the ambition target, stated as a claim.** _Not a budget figure: the actual sentence a strong version of this paper gets to write. "A strong result shows X across Y conditions / Z seeds with W baselines and a downstream-transfer test; a weak result shows X in one setting with one seed." This is the claim the fully-deployed budget should be able to buy (cost it in § Budget-deployment menu): the target is a result you can claim, and not reaching it with budget left over is the under-spend defect (`BRIEF.md` § Budgets; `AGENTS.md` § Resources). Retire or shrink the target only with a triggered reason (cap exhausted, mechanism dead), logged like a claim demotion — never "the cheap scout already settled it."_
- **The single most decisive experiment.** _The one run that would move the question most — the one that actually settles it, not the cheapest one that technically clears the floor (`playbooks/review.md` §2d). Name it, and what each outcome would mean._
- **The known cruxes.** _The 2–4 open questions any reviewer will press on (the OPEN CRUXES that recurring literature work is aimed at, `playbooks/exploration.md` §2). These drive the back-edges below: a Review or Draft finding that lands on a crux sends you back to Experiment, which is normal research._

**Headline claim (base):** _the claim the deliverable will make, locked once the direction is chosen in the Exploration Dossier (not at hour 0), and tracked in `REGISTRY.md` § Claim & decision trail._
**Headline claim (stretch):** _the stronger form (the ambition-target claim above), with its falsifier registered. Settling for the base without having run the stretch falsifier requires a logged reason (usually cost). Don't abandon a claim that hasn't been falsified; don't keep one that has._

## Budget-deployment menu (what the budget buys)

_Write at plan time, alongside the spine. The budget is a target to deploy (`BRIEF.md` § Budgets); this menu makes *not* spending visibly leave named items on the table. A costed shopping list of what the GPU/API budget buys toward the ambition-target claim — model scale, seed count, datasets, ablation depth — so the decisive experiment and the strong-result claim are concretely affordable, not aspirational. Revisit at each milestone against burn-rate-vs-runway (`HEARTBEAT.md`). A cheap scout screens direction; these items are what make the eventual headline **powered** (`locks/evidence_floors.json`)._

| item (what it buys) | approx cost (API / GPU / time) | which claim it strengthens | status (planned / running / bought / declined+reason) |
|---|---|---|---|

## Candidate portfolio

_Filled during planning, before any headline commitment: 2–3 ranked approach candidates spanning the design space the brief points at. **Committing to a headline approach requires scout results from at least two.** When the leading candidate fails at the mechanism level (`playbooks/decisions.md` § Failure levels), the default next action is advancing the next candidate; re-scoping to a different kind of contribution instead is a Tier-2 memo. A candidate is cheap to keep alive (one table row) and expensive to resurrect from nothing — keep the losers' rows current. A candidate is marked killed or promoted by a scout that returns a clean FAIL/PASS against its pre-registered kill/promotion criterion (`AGENTS.md` § Pre-registration); an **AMBIGUOUS, MALFORMED, or single-seed / under-powered scout does not settle it — deepen or re-run before narrowing the headline** (that's pro-depth: a cheap inconclusive scout is not a decision). The portfolio is the `## HYPOTHESIS PORTFOLIO` of the Exploration Dossier (`playbooks/exploration.md`); committing to a headline — and entering drafting — needs not just ≥2 scouts but the exploration-sufficiency critic's ADEQUATE verdict (`playbooks/exploration.md` §4), honored cooperatively. Exhausting the portfolio with only one viable direction (the others settled by clean FAIL) is honest convergence, not a stall._

| rank | candidate (mechanism premise) | cheapest scout | kill criterion | promotion criterion | status |
|---|---|---|---|---|---|

## Milestone table — the research cycle

_Write within ~2 hours of reading `BRIEF.md`; log as the first Tier-2 memo. The lifecycle is a **cycle, not a staircase**:_

`Set up → Explore (deep-read lit + scouts) ⇄ Experiment ⇄ Draft ⇄ Review → Readability → Ship`

_The `⇄` back-edges are **normal research, not failures**: a Review or Draft finding that lands on a crux (§ Research plan) sends you back to Experiment — the default response to a real finding is "what experiment closes this?", not "narrow the claim" (`playbooks/review.md`). Rules:_
- _3–6 milestones, each with an absolute deadline and a gate command that must pass; every gate includes the brief-fidelity review (`playbooks/review.md` §3)._
- _Calibrate the schedule to the horizon: the Exploration Dossier milestone deserves roughly 30–40% of the total run (on a 5-day deadline, land it around day 1.5–2). A short horizon compresses drafting and review, not exploration — a thin dossier poisons everything downstream (`playbooks/exploration.md`)._
- _Milestone 1: deliverable skeleton compiling in the final target format (an empty shell built from the venue template `templates/paper_template.zip`), page-budget gate live, plan/falsifiers registered. The shell does **not** authorize prose — its gate is the plain skeleton-mode `gate_artifact.sh`. Drafting is gated **cooperatively** by the exploration-sufficiency critic (§4), not by the shell (keeping the skeleton early preserves the target-format-from-day-1 anti-failure mechanism)._
- _Exploration precedes drafting: a gated **Exploration Dossier** milestone (`playbooks/exploration.md`) — lit synthesis + ≥2 candidates scouted to clean verdicts + the chosen direction endorsed by the isolated sufficiency critic — comes before any drafting milestone._
- _Drafting milestones follow `playbooks/writing.md` — compressed narrative first; no full prose while a headline number lacks its `REGISTRY.md` falsifier/claim row. Stage-1 prose may not begin until the **exploration-sufficiency critic returns ADEQUATE** (`playbooks/exploration.md` §4) — a cooperative gate honored by the critic's `reviews/*.md` verdict, not a cert flag. The depth question ("is the headline powered enough?") is not deferred: it is owed to the **ship-time power critic** at the final gate (`playbooks/review.md` §2d), and any deferred power item (scale/seeds/robustness) is carried as a costed row in § Budget-deployment menu so it can't be quietly dropped._
- _The **final gate** runs `REQUIRE_EXTERNAL_REVIEWS=1 REQUIRE_EVIDENCE_ADEQUATE=1 REQUIRE_SHIP_AUTHORIZATION=1 REQUIRE_PRESENTATION=1 REQUIRE_README=1 scripts/gate_artifact.sh <pdf>` — externals present, the isolated power critic's `reviews/power_critic_ship.md` ADEQUATE against the delivered artifacts, the light under-spend backstop satisfied, the presentation gates still passing at ship, and the cold-visitor README in place._
- _The **final README** is the run's last commit: after the Presentation Overhaul and before the completion report, write the repo-root README for a visitor with zero context (`playbooks/writing.md` § The final README), pass its gate + one cold-reader, commit, then send the completion report._
- _The **Presentation Overhaul** is a mandatory terminal milestone after Review converges and before Ship (`playbooks/writing.md` § The Presentation Overhaul). Presentation-level restructuring is authorized; claims, numbers, and evidence are frozen (registry + brief-fidelity checked after). Its gate is an acceptance test, not a self-judgment: `REQUIRE_PRESENTATION=1 scripts/gate_artifact.sh` passing AND two fresh PDF-only cold-readers correctly extracting claim / evidence standard / significance with no undefined terms. It may not be skipped, compressed into the Ship milestone, or passed by self-assessment — under deadline pressure it is the milestone deadlines that move, never this gate._

| # | Milestone | Deadline (absolute) | Gate (command that must pass) | Status |
|---|---|---|---|---|
| 1 | Env verified + deliverable skeleton compiles in target format (empty shell) + page-budget gate live + plan/falsifiers registered | | `scripts/gate_artifact.sh <pdf>` (skeleton mode) | |
| 2 | Explore: Exploration Dossier — deep-read lit synthesis + ≥2 candidates scouted to clean verdicts + direction critic-endorsed ADEQUATE (`playbooks/exploration.md` §4) | | `scripts/gate_artifact.sh <pdf>` + sufficiency critic ADEQUATE (cooperative) | |
| 3 | Experiment ⇄ Draft: headline direction committed + first full draft begins (back-edge to Experiment on any crux finding is normal) | | `scripts/gate_artifact.sh <pdf>` + sufficiency critic ADEQUATE | |
| 4 | Review: hostile/blind/external rounds converge (`playbooks/review.md`); back-edges to Experiment expected | | `REQUIRE_EXTERNAL_REVIEWS=1 scripts/gate_artifact.sh <pdf>` | |
| 5 | Presentation Overhaul: restructuring-authorized rewrite for cold-reader legibility, claims frozen (`playbooks/writing.md` § The Presentation Overhaul) | | `REQUIRE_PRESENTATION=1 scripts/gate_artifact.sh <pdf>` + 2 fresh PDF-only cold-readers pass the acceptance test | |
| 6 | Ship: externals reviewed + camera-ready + accompanying final review + power critic ADEQUATE + presentation gates hold + final README written, cold-read, and committed as the last commit (`playbooks/writing.md` § The final README) | | `REQUIRE_EXTERNAL_REVIEWS=1 REQUIRE_EVIDENCE_ADEQUATE=1 REQUIRE_SHIP_AUTHORIZATION=1 REQUIRE_PRESENTATION=1 REQUIRE_README=1 scripts/gate_artifact.sh <pdf>` | |

## Work queue (current milestone)

_The middle altitude between the milestone table and the in-flight capsule: every real piece of work toward the current milestone gets a row the moment it's conceived — no mental notes. One row per coherent task: don't fragment one task across five rows or bury five tasks in one. Rows are maintained by the turns that touch them — a LAUNCH/DELEGATE turn opens or advances its row, a harvest closes it — and the harvest-check heartbeat task catches drift. If the queue is empty but the milestone gate doesn't pass, that's a planning error: fix the queue, not the gate._

| task | owner (main / subagent / process) | budget | status | blocked on |
|---|---|---|---|---|

## Decision-independent backlog

_Standing list of useful work requiring no pending decision and no operator input. Pull from here during every Tier-2 memo window and Tier-3 block. Keep ≥3 items at all times; when it runs dry, generating more backlog IS the next task._

- Reproducibility hardening (one-command repro of current headline numbers)
- Robustness/ablation depth on already-locked claims (deepen toward the ambition target — more seeds/scale before polish)
- Lit-gap closure (verify any citation still marked unverified; deep-read any anchor paper still at abstract-only)
- Documentation/readme debt
