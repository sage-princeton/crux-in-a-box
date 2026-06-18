# REGISTRY.md — Pre-registrations, Locks, Audited Numbers, Review Ledger

_Append-only (statuses may be updated in place; rows are never deleted). This file is the audit surface: everything here is checkable in one pass. The rewriteable plan is `PLAN.md`; when they disagree, this file wins._

## Conventions (falsifier statuses)

- **Pre-registered:** declared *before* the experiment that could trigger it ran.
- **Post-hoc:** declared after (allowed, but must say so explicitly).
- **Triggered:** failure condition met — the hypothesis is in trouble.
- **Not triggered:** condition tested and not met.
- **Vacuously not triggered:** the antecedent never held; the test carries no signal.

## Brief constraints

_Filled at plan time: every load-bearing requirement and every "do not" in `BRIEF.md` gets a row, with the mechanism that checks it (a gate, a registered falsifier/ablation, or the brief-fidelity review). A constraint with no checking mechanism is a constraint that will erode. If a required ablation comes back MARGINAL or INCONCLUSIVE, the constraint is **triggered** — a stuck/pivot trigger, not a caveat for the limitations section._

| brief constraint (requirement or prohibition) | checked by | status |
|---|---|---|

## Falsifiers

_One block per method claim. Every claim in `PLAN.md` § Current approach has a row here before its first experiment runs._

### F-1 — [name] (`<trigger condition, stated as an inequality or event>`)
- **Hypothesis it would falsify:**
- **Pre-registered:** [LOG.md entry key] — [pre-registered | post-hoc]
- **Executed on:** [experiment / artifact paths]
- **Status:**

## Headline substance (drafting-entry certification)

_Checked by `scripts/gate_artifact.sh` (`REQUIRE_HEADLINE_SUBSTANCE=1`) at the skeleton/drafting milestone gate. Drafting cannot begin until the locked headline clears it. Add exactly one line, e.g.:_

`HEADLINE-SUBSTANCE: non-vacuous=yes; not-by-construction=yes; certified-by=MEMO-x / <subagent>`

- **non-vacuous=yes** — a runnable experiment could falsify the headline (its pre-registered falsifier above is *not* "Vacuously not triggered"). A vacuous falsifier means the claim is true by construction → Stuck/Pivot (`AGENTS.md` § Pre-registration), not a claim to draft around.
- **not-by-construction=yes** — required (not `n/a`) when the headline is a negative/impossibility/boundary result: an *independent isolated subagent* answered "is this claim true by construction given its own definitions?" → **NO**. Record the subagent/LOG ref in `certified-by`.

## Exploration adequacy (drafting-entry certification)

_Checked by `scripts/gate_artifact.sh` (`REQUIRE_EXPLORATION_ADEQUATE=1`) at the dossier milestone and the drafting-entry milestone. Prose drafting cannot begin until exploration is critic-certified adequate (`playbooks/exploration.md` §4–§5). Add exactly one line, e.g.:_

`EXPLORATION-ADEQUATE: settled-candidates=2; lit-coverage=yes; certified-by=<critic subagent / LOG ref>`

- **settled-candidates=N** — N ≥ 2 candidates settled by a *clean* PASS/FAIL against their pre-registered criterion (a clean FAIL counts — killing an alternative is settled exploration; AMBIGUOUS/single-seed does not count, per `PLAN.md` scout-depth). For a genuinely single-viable-idea run, use `exhaustion-memo=<id>` instead — a critic-affirmed honest convergence (`playbooks/decisions.md` § Failure levels).
- **lit-coverage=yes** — written only after the isolated exploration-sufficiency critic returns **ADEQUATE** (not THIN-LIT / STOP-EARLY / FABRICATED-OR-VACUOUS). The critic, not any self-assessment, authorizes this line.

## Claim ledger

_The headline claim is locked at plan time like any other definition (base + stretch forms, see `PLAN.md`). Every subsequent narrowing gets a row here. **Rule (AGENTS.md evidence rule 7): a claim may not shrink without a triggered falsifier or a logged cost argument.** A demotion row citing neither is a hedging defect — restore the claim or produce the evidence. If cumulative demotions change the contribution, that's a Tier-2 memo._

| date | claim before → after | forced by (falsifier id / cost argument / memo id) |
|---|---|---|

## Locks

_Index of frozen definitions. The values live as JSON in `locks/` and are read by code (see `locks/README.md`). Changing a lock = Tier-2 memo._

| Lock file | What it freezes | Locked at | Changed (memo id) |
|---|---|---|---|

## Audited numbers

_One row per load-bearing statistic, added the first time the number appears in prose (AGENTS.md evidence rule 2). Every row is re-verified by `scripts/gate_artifact.sh`'s number sweep before each review round and milestone gate; update last-verified then._

| statistic-name | re-derivation script | source artifact | expected value | last-verified |
|---|---|---|---|---|

## Review ledger

_Endgame blind rounds only (`playbooks/review.md` §2a). Stop after 2 consecutive clean rounds or round 6. A central-claim rejection recurring across ≥2 rounds forks to Stuck/Pivot — mark the `central-claim hit?` column Y and do not spawn the next round._

| round | date | verdict | clean? (0 soundness/claim findings) | central-claim hit? (Y/N) |
|---|---|---|---|---|
