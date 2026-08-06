# Playbook: Decisions

Why this exists: the dominant time loss in long-horizon autonomous work is not wrong decisions but unmade ones — and there is no one to ask. The operator is an observer (`USER.md`); every decision is yours. What protects a hard decision is not approval but **discipline**: a logged memo, adversarial critique where the stakes warrant it, and a reversible default taken immediately.

## Tier-2 procedure (logged decision memo)

A decision is Tier 2 when getting it wrong would change the project's contribution, a `locks/` definition, or the milestone schedule — and the brief, the plan, and the evidence don't settle it.

1. **Write the memo** in `LOG.md`:

   ```
   MEMO <id> — <one-line question>
   Options:
     A) <option> — evidence for/against, cost
     B) <option> — evidence for/against, cost
   Decision: <A or B>, because <one sentence>.
   Reversal: taken on branch <name>; <the evidence that would reverse it>.
   ```

2. **If it changes the contribution** (headline claim, portfolio re-rank, lock change): run the critic pass from § Stuck/Pivot — one critic subagent per live option — *before* deciding. Otherwise decide directly; the memo is the deliberation.

3. **Proceed immediately** on a git branch; merge when the next evidence point confirms the direction. Revisit only if the named reversal evidence appears. Writing a memo and yielding with nothing in flight violates the End-of-Turn Contract.

**Anti-patterns, banned by name:**
- *Maturation horizon* — "wait N hours, then think again." The memo is the thinking; there is nothing to wait for.
- *Ad-hoc pings* — status belongs in the scheduled snapshots; questions to the operator belong nowhere.
- *Speculative pre-commitment* — building out both branches of an undecided memo. Decide, then build.

## Stuck / Pivot procedure

Tripwire (from `AGENTS.md`): same blocker >2h, or 3 failed attempts at the same approach, or accumulating evidence that the current hypothesis is dead — including a **central-claim rejection recurring across ≥2 blind-review rounds** (`playbooks/review.md` §2a), a **DIVERGED** brief-fidelity verdict (`playbooks/review.md` §3), or a headline whose pre-registered falsifier is **vacuous**.

### Returning to the Experiment phase is the normal move, not the fork

Most findings never reach this procedure. The research lifecycle is a cycle — `… ⇄ Experiment ⇄ Draft ⇄ Review` (`PLAN.md` § Research plan) — and the `⇄` back-edges are **normal research, not failures**. The default response to a real soundness or power finding in Review or Draft is *"what experiment closes this?"* — return to the Experiment phase and run it (`playbooks/review.md` triage). That is forward motion, the expected shape of a good run, and it does **not** require a Stuck/Pivot memo. This procedure is for the narrower case where the *approach itself* is failing — where re-running the same experiment won't help — not for every result that sends you back to the bench.

### Failure levels (diagnose before deciding)

Persist-vs-pivot is not a mood; classify what actually broke:

- **Implementation-level** — the code, a hyperparameter, the experimental setup. Persist: fix and rerun. (This includes the common, healthy case: a Review/Draft finding routes you back to the Experiment phase to run the experiment that closes it — a sanctioned back-edge, not a pivot.)
- **Mechanism-level** — the idea's premise broke: the signal you hypothesized structurally isn't there. Stop repairing. Default: advance the next `PLAN.md` portfolio candidate. Repairing a mechanism-level failure with implementation-level fixes is how a large fraction of the horizon disappears.
- **Question-level** — the portfolio is exhausted, or the evidence genuinely re-scopes the brief's question. Tier-2 memo (it changes the contribution), with the brief-fidelity review (`playbooks/review.md` §3) run on the re-scoped direction before committing.

The misclassification risk runs one way in practice: mechanism-level failures get treated as implementation-level (endless local repair), then jump straight to question-level (re-frame the deliverable around surviving artifacts) without ever passing through "advance the next candidate." The portfolio exists to make that middle step the cheap default.

A **recurring central-claim rejection in blind review** is mechanism-level by definition — reviewers are rejecting the headline's *premise*, which presentation fixes, more datasets, and claim-narrowing cannot repair. Resolve it by **advancing the next portfolio candidate**. Re-scoping around surviving audited artifacts (the failed direction becomes an honest negative result / appendix, not discarded) is the question-level move, taken only once **both** the portfolio **and** the strong result the budget could buy are exhausted — i.e. the next candidate is settled by a clean verdict *and* the powering/scale-up lever (`PLAN.md` § Research plan, the strong-result target; e.g. a larger-model or more-seed arm) has actually been **run**, not merely costed and declined. A full restart from scratch is the last resort, when neither a surviving candidate nor a re-scope salvages. Every step is the Tier-2 memo + critic pass below, and the menu is bounded by the portfolio — so the fork resolves in one decision and cannot thrash.

**Two failure modes this fork is built to block:**
- *Downward re-scope while power remains.* Re-scoping the headline to a **thinner kind of contribution** (an empirical result → a methodology/harness paper; a positive claim → a bare negative) while a costed power/scale experiment or an un-scouted candidate is still available is **banned** — it is the question-level move taken before its preconditions hold. An honest negative result is a legitimate contribution only once it is itself *powered* to the evidence floors (`locks/evidence_floors.json`, ship gate `REQUIRE_EVIDENCE_ADEQUATE` via the isolated power critic, `playbooks/review.md` §2d); a negative resting on a single underpowered cell is not a contribution, it is an unfinished experiment.
- *"Grinding" used to skip an unrun experiment.* The Stuck tripwire stops repeating a **tried-and-failed** approach. A strengthening lever you have **not run** — the bigger model, the extra seeds, the next candidate — is forward motion you owe, not grinding; declining it on "that's grinding / already adjudicated" grounds is a misuse (`AGENTS.md` § Stuck tripwire). That call belongs to the under-spend memo (`HEARTBEAT.md` milestone-clock), which may not resolve to "ship" while a cap and the strong-result target (`PLAN.md` § Research plan) both have room (ship gate: `REQUIRE_SHIP_AUTHORIZATION`).

1. **Write the decision memo to yourself first** (in `LOG.md`): current approach, evidence it's failing, 2–3 alternatives, cost of each, and the kill criterion that would settle it. The alternatives lead with the back-edge moves the cycle prefers — "run the experiment that closes the finding" and "advance the next portfolio candidate"; "narrow the claim" is listed as the *fallback*, taken only when the closing experiment is genuinely out of budget (not as a first resort while power remains, per the bans above).
2. **Spawn one critic subagent per live option** (isolated context; give each the memo + pointers to `LOG.md`/`PLAN.md`/relevant artifacts). Each critic's brief: argue *for its assigned option and against the others*, grounded in the artifacts — no new experiments, 30–45 min budget.
3. **Decide yourself, this heartbeat.** Weigh the critics, pick, log Observed/Decided/Reason, update `PLAN.md` (and `REGISTRY.md` if a falsifier or lock changes — lock changes are Tier 2).
4. **Escalate only by tier.** A pivot that changes the contribution is a Tier-2 memo (with your chosen direction as the default — you still proceed). It is Tier 3 only if an external resource is what's broken.

The point: getting stuck triggers a *procedure that ends in a decision*, not a message that ends in a wait.

## Tier-3 conduct (the only operator contact, and the only legitimate waits)

Qualifies: a critical external resource broken after a documented debugging attempt (including screenshot-when-hung from `TOOLS.md`); an imminent budget-cap breach; the completion report. Format and content per `USER.md`.

While blocked: drain the `PLAN.md` backlog. When the backlog empties, generate more (reproducibility hardening, robustness checks, ablation depth, documentation, lit-gap closure — there is always legitimate backlog on a research project). Log "idle" only when that is exhausted too. Re-test the broken resource on the blocker-recheck cadence; when it recovers, log and resume.
