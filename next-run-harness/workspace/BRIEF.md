# BRIEF.md — The Task

Research Question: Can large language model (LLM) personas be decomposed, measured, and controlled as positions in a structured "trait space" using weight-space interventions?

Relevant Context: LLMs often exhibit stable behavioral patterns ("personas") that affect how they generalize out of distribution and after fine-tuning, and these patterns are important for safety reasons, modulating, for instance, the model’s propensity to reward hack or take unsanctioned actions during training. Current control methods are either brittle (prompting, steering) or expensive/inflexible (full retraining). We lack tools to decompose personas into independently controllable components, measure them rigorously, and compose them, except in the case of activation steering, which is flawed for a variety of reasons. The agent should produce (a) a method for inducing targeted behavioral shifts using weight-based, rather than activation-based, interventions, (b) evidence about whether the induced dimensions are independent/composable, and (c) at least one test of whether these dimensions affect a downstream behavior the agent didn't directly train for.


## Goal & success bar

The deliverable is a research paper answering the research question above, of a quality **publishable at NeurIPS** (a top-tier ML venue). "Publishable" is operationalized through the peer-review hierarchy in `playbooks/review.md`: **success is a review score of Weak Accept or higher** — from the internal blind review (§2a / the accompanying final review §2c) and the external reviewers (§2b), whose verdicts you cannot author.

- A converged verdict **below** Weak Accept is *not* success. While budget and time remain, treat it as a mechanism/claim-level failure and route through the stuck/pivot fork (`playbooks/decisions.md`; `playbooks/review.md` §2a) — re-scope or advance a portfolio candidate; do **not** keep polishing a sub-bar draft.
- **Never manufacture the score.** Reviewers are isolated and unauthorable (`review.md` §2a); the only honest path to Weak Accept is genuinely meeting the bar. If the budget or deadline is exhausted while still below bar, ship the honest verdict as written and say so plainly in the completion report — an honest below-bar result beats a gamed Accept.

## Budgets & caps

You have a finite budget; pursue the success bar until you meet it or a cap is reached — neither finishing far under budget nor breaching a cap is acceptable.

- **Time:** {{DEADLINE|two weeks from launch}} — tracked by the `HEARTBEAT.md` milestone-clock against your milestone schedule.
- **API spend:** {{API_BUDGET}} — measured by `scripts/telemetry_costs.py`.
- **GPU compute (RunPod):** {{CLOUD_SPEND_LIMIT}} — measured per `TOOLS.md` § Spend measurement; terminate idle pods.

The pre-flight gate and threshold ladder in `AGENTS.md` § Resources act on these numbers; breaching a cap is a Tier-3 event.

## How success is verified (the tools)

- **Quality / the bar:** the peer-review hierarchy in `playbooks/review.md` — hostile section reviews (§1), underclaim audit (§1b), internal blind rounds (§2a), external reviews (§2b), accompanying final review (§2c), brief-fidelity review (§3). The blind-review score against the Weak Accept bar is the success signal; external reviews (§2b) are its strongest form.
- **Form / hygiene:** `scripts/gate_artifact.sh` — page budget, placeholders, deanonymization, headline-substance, exploration-adequacy, external-review presence, registered-number sweep.
- **Budget / time:** the scripts named above.

Register the success bar and each cap as a row in `REGISTRY.md` § Brief constraints with its checking mechanism (hour-0 duty).