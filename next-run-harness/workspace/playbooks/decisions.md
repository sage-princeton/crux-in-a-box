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

### Failure levels (diagnose before deciding)

Persist-vs-pivot is not a mood; classify what actually broke:

- **Implementation-level** — the code, a hyperparameter, the experimental setup. Persist: fix and rerun.
- **Mechanism-level** — the idea's premise broke: the signal you hypothesized structurally isn't there. Stop repairing. Default: advance the next `PLAN.md` portfolio candidate. Repairing a mechanism-level failure with implementation-level fixes is how days disappear.
- **Question-level** — the portfolio is exhausted, or the evidence genuinely re-scopes the brief's question. Tier-2 memo (it changes the contribution), with the brief-fidelity review (`playbooks/review.md` §3) run on the re-scoped direction before committing.

The misclassification risk runs one way in practice: mechanism-level failures get treated as implementation-level (endless local repair), then jump straight to question-level (re-frame the deliverable around surviving artifacts) without ever passing through "advance the next candidate." The portfolio exists to make that middle step the cheap default.

A **recurring central-claim rejection in blind review** is mechanism-level by definition — reviewers are rejecting the headline's *premise*, which presentation fixes, more datasets, and claim-narrowing cannot repair. Resolve it by **advancing the next portfolio candidate**. Re-scoping around surviving audited artifacts (the failed direction becomes an honest negative result / appendix, not discarded) is the question-level move, taken only once the portfolio is exhausted; a full restart from scratch is the last resort, when neither a surviving candidate nor a re-scope salvages. Every step is the Tier-2 memo + critic pass below, and the menu is bounded by the portfolio — so the fork resolves in one decision and cannot thrash.

1. **Write the decision memo to yourself first** (in `LOG.md`): current approach, evidence it's failing, 2–3 alternatives (including "narrow the claim" and "pivot the method"), cost of each, and the kill criterion that would settle it.
2. **Spawn one critic subagent per live option** (isolated context; give each the memo + pointers to `LOG.md`/`PLAN.md`/relevant artifacts). Each critic's brief: argue *for its assigned option and against the others*, grounded in the artifacts — no new experiments, 30–45 min budget.
3. **Decide yourself, this heartbeat.** Weigh the critics, pick, log Observed/Decided/Reason, update `PLAN.md` (and `REGISTRY.md` if a falsifier or lock changes — lock changes are Tier 2).
4. **Escalate only by tier.** A pivot that changes the contribution is a Tier-2 memo (with your chosen direction as the default — you still proceed). It is Tier 3 only if an external resource is what's broken.

The point: getting stuck triggers a *procedure that ends in a decision*, not a message that ends in a wait.

## Tier-3 conduct (the only operator contact, and the only legitimate waits)

Qualifies: a critical external resource broken after a documented debugging attempt (including screenshot-when-hung from `TOOLS.md`); an imminent budget-cap breach; the completion report. Format and content per `USER.md`.

While blocked: drain the `PLAN.md` backlog. When the backlog empties, generate more (reproducibility hardening, robustness checks, ablation depth, documentation, lit-gap closure — there is always legitimate backlog on a research project). Log "idle" only when that is exhausted too. Re-test the broken resource on the blocker-recheck cadence; when it recovers, log and resume.
