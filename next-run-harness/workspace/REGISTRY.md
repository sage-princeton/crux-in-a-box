# REGISTRY.md — Light Pre-registration Surface

_A **light** record, not an append-only contract. It holds the pre-registration worth the ceremony: a falsifier declared **before** the experiment that could trigger it, an index of frozen `locks/`, and a short trail of how the headline claim changed and why. The ship gates read the isolated critic's own artifact (`reviews/power_critic_ship.md`, `reviews/final_review.md`) and `locks/budget.json` directly, so nothing here needs a parseable grammar. The rewriteable plan is `PLAN.md`; the full reasoning trail is `LOG.md`._

_Keep this file cheap. If maintaining it costs more than a few minutes a day, you are over-recording — move the prose to `LOG.md` and leave only the falsifiers, the locks index, and the claim trail here._

## Falsifiers

_One block per method claim in `PLAN.md` § Research plan — declared **before** its first experiment runs. This is the one pre-registration worth the ceremony: it stops post-hoc storytelling. Statuses: pre-registered (declared before the experiment) · triggered (failure condition met) · not-triggered (tested, not met) · vacuous (the antecedent never held — the test carries no signal; a vacuous headline falsifier is a Stuck/Pivot trigger, not something to draft around)._

### F-1 — [name] (`<trigger condition, as an inequality or event>`)
- **Hypothesis it would falsify:**
- **Pre-registered:** [LOG.md entry key]
- **Executed on:** [experiment / artifact paths]
- **Status:**

## Locks

_Index of frozen definitions; values live as JSON in `locks/` and are read by code (`locks/README.md`). Changing a lock = Tier-2 memo._

| Lock file | What it freezes | Locked at | Changed (memo id) |
|---|---|---|---|
| `locks/budget.json` | budget-deployment target + run clock | hour-0 | |
| `locks/evidence_floors.json` | min seeds / load-bearing cells for the headline | hour-0 | |

## Claim & decision trail

_A short living list — the headline claim as locked at plan time (base + stretch, see `PLAN.md`), and each subsequent change with its one-line forcing reason (a triggered falsifier, a cost argument, or a logged memo). Symmetric claim discipline (`AGENTS.md`): don't narrow a claim without a triggered falsifier or a logged cost reason, and don't keep one its falsifier killed. If a change alters the contribution, it's a Tier-2 memo in `LOG.md`._

| date | claim before → after | forced by (falsifier / cost / memo id) |
|---|---|---|
