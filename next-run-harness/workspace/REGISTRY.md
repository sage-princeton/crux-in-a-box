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

_Endgame blind rounds only (`playbooks/review.md` §2a). Stop after 2 consecutive clean rounds or round 6._

| round | date | verdict | clean? (0 soundness/claim findings) |
|---|---|---|---|
