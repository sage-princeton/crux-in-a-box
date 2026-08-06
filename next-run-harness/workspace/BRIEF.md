# BRIEF.md — The Task

Research Question: {{RESEARCH_QUESTION}}

Relevant Context: {{RESEARCH_CONTEXT}}


## Goal & success bar

The deliverable is a research paper answering the research question above, of a quality **publishable at NeurIPS** (a top-tier ML venue). "Publishable" is operationalized through the peer-review hierarchy in `playbooks/review.md`: **success is a review score of Weak Accept or higher** — from the blind review (§2a) and the accompanying final review (§2c), whose verdicts you cannot author. All review is internal: isolated critics you spawn, never a third-party service.

- A converged verdict **below** Weak Accept is *not* success. While budget and time remain, treat it as a mechanism/claim-level failure and route through the stuck/pivot fork (`playbooks/decisions.md`; `playbooks/review.md` §2a) — re-scope or advance a portfolio candidate; do **not** keep polishing a sub-bar draft.
- **Evidence-adequacy floor (the headline must be powered).** A headline — positive *or* negative — may not rest on a single observation (one cell / one seed / one datapoint) and must meet the seed/cell floors in `locks/evidence_floors.json`. An underpowered headline is an **evidence-adequacy failure, not a significance caveat**: it routes to Stuck/Pivot or gets powered up, and it is *not* dischargeable by the §2a significance tie-break. At ship this floor is certified by the **isolated power critic** (`playbooks/review.md` §2d), whose verdict file `reviews/power_critic_ship.md` (verdict line `ADEQUATE`, with the seed/cell counts it found against the delivered artifacts) the gate reads directly — `scripts/gate_artifact.sh REQUIRE_EVIDENCE_ADEQUATE=1`. You author the experiments, not the verdict. A negative result faces the *same* floor as a positive one (`AGENTS.md` § Pre-registration).
- **Never manufacture the score.** Reviewers and critics are isolated and unauthorable (`review.md` §2a, §2d) — and since there is no third-party reviewer to appeal to, that isolation discipline *is* the whole honesty mechanism; the only honest path to Weak Accept is genuinely meeting the bar. Shipping an honest below-bar verdict is licensed **only** when a cap is genuinely (near-)exhausted — a light backstop the gate checks by reading `reviews/final_review.md`, `locks/budget.json`, and an honest ship/under-spend memo in `LOG.md` directly (`scripts/gate_artifact.sh REQUIRE_SHIP_AUTHORIZATION=1`; `HEARTBEAT.md` burn-rate beat). With budget and time still comfortable, "ship the honest below-bar result" is **not** an available move — keep working. An honest below-bar result beats a gamed Accept; an early below-bar exit with the budget unspent beats neither.

## Budgets & caps

You have a finite budget; **it is a target to deploy, not just a ceiling to stay under.** Pursue the success bar until you meet it or a cap is reached — and reaching a ship-candidate state with most of the budget unspent is an under-spend defect with the *same status as overclaiming* (`AGENTS.md` § Resources, the felt-budget heuristic; `HEARTBEAT.md` burn-rate vs runway). Spend on depth before polish. State the strongest result the budget could buy as a claim in `PLAN.md` § Research plan (the ambition target — what a *strong* result looks like vs. a weak one) and back it with the costed deployment menu (`PLAN.md` § Budget-deployment menu); retire that target only with a triggered reason, exactly as for a falsifier. The under-spend backstop is light and gate-checked at ship (`scripts/gate_artifact.sh REQUIRE_SHIP_AUTHORIZATION=1`); the felt-budget heuristic does the real work.

- **Time:** {{DEADLINE|1 day from launch}} — the **run horizon**, and the unit every schedule in the scaffold is expressed in. Milestones, the exploration fraction, and the crunch threshold are all stated as *fractions of this horizon*, never as fixed hours, so the same scaffold works at one day or two weeks. Tracked by the `HEARTBEAT.md` milestone-clock against your milestone schedule. Hour-0 duty: convert it to an absolute `deadline_iso` in `locks/budget.json` alongside `launch_iso`, and work in fractions off that pair.
- **API spend:** {{API_BUDGET}} — measured by `scripts/telemetry_costs.py`.
- **GPU compute (RunPod):** {{CLOUD_SPEND_LIMIT}} — measured per `TOOLS.md` § Spend measurement; terminate idle pods.

The pre-flight sanity note and burn-rate heuristic in `AGENTS.md` § Resources act on these numbers; breaching a cap is a Tier-3 event.

## How success is verified (the tools)

- **Quality / the bar:** the peer-review hierarchy in `playbooks/review.md` — hostile section reviews (§1), underclaim audit (§1b), blind rounds (§2a), accompanying final review (§2c), power critic (§2d), brief-fidelity review (§3). The blind-review score against the Weak Accept bar is the success signal.
- **Form / hygiene (`scripts/gate_artifact.sh`, always on):** `pdf-exists`, `page-budget`, `placeholders`, `internal-vocab`, `deanonymize`, `internal-paths`.
- **The surviving substance gates (`scripts/gate_artifact.sh`, final/ship):**
  - `REQUIRE_EVIDENCE_ADEQUATE=1` — the ship-time power critic's `reviews/power_critic_ship.md` exists, its verdict reads `ADEQUATE`, and it reports `seeds≥seed_floor` / `cells≥cell_floor` (`locks/evidence_floors.json`). This makes the "powered enough" clause mechanical rather than self-judged, with no agent-typed token in the loop.
  - `REQUIRE_SHIP_AUTHORIZATION=1` — the light under-spend backstop (don't finish far under budget while time and money remain): reads `reviews/final_review.md`, `locks/budget.json`, and an honest ship/under-spend memo in `LOG.md`.
  - `REQUIRE_PRESENTATION=1` — the presentation gates (abstract within its word cap, no ALL-CAPS prose, at least one main-body figure), active at the Presentation Overhaul milestone and re-checked at ship. The full acceptance test — these gates plus two fresh PDF-only cold-readers — is in `playbooks/writing.md` § The Presentation Overhaul; a paper that fails it does not ship, regardless of its review scores.
  - `REQUIRE_README=1` — the final README exists at repo root, is substantive, includes a reproduction section, and carries no internal vocabulary. Written and committed as the run's **last commit**, after the Presentation Overhaul and before the completion report (`playbooks/writing.md` § The final README).
- **Budget / time:** the scripts named above.

Hour-0 duty: complete the gate-read locks (`locks/budget.json`, `locks/evidence_floors.json`; see `locks/README.md`) with this brief's caps and the actual launch clock, and index them in `REGISTRY.md` § Locks.
