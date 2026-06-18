# Playbook: Review

Two distinct review regimes: §1 is cheap and reliably catches fabrication; §2 devolves without structural rules. The "why" notes are load-bearing — read them.

## §1 — Drafting reviews (hostile section review, during writing)

Use whenever a load-bearing section/version of the deliverable lands (intro, method, theory, experiments — anything other sections will cite).

- **Spawn a fresh isolated reviewer subagent** per version: input = the section file(s) + the claim-anchor files it must check against (locks, registry, run artifacts). Brief: *find reasons to reject* — hunt specifically for (a) summary statistics not re-derived from artifacts, (b) claims that outrun their pre-registered falsifiers, (c) overclaim relative to the locked scope, (d) notation/definition drift against sibling sections, (e) narrative incoherence — prose that doesn't serve the claims/evidence/so-what frame or disagrees with the Claim ledger (`playbooks/writing.md`). Budget ~30–45 min. Output: `reviews/review_<section>_v<N>.md` with MAJOR/minor findings.
- **You triage** (the reviewer never does): SHIP (fix now — inline if <10 lines, revision subagent otherwise), DEFER (queue with a named owner-milestone), ESCALATE (Tier-2 memo if it reopens a locked scope decision).
- **Cycle until the section is audit-stable:** review → fix → review. Two consecutive reviews with no MAJOR on a section = stable; move on. (Hostile section reviews catch confabulated statistics within hours of writing — the cheapest truth-enforcement in the harness. Do not skip them to save time.)

### §1b — Underclaim audit (the counter-force)

Hostile reviews only push one direction: cut, demote, narrow. Run unopposed, they shrink the headline claim round after round until the deliverable is over-pruned. So, **once per drafting cycle on each load-bearing section, and once on the full draft before the endgame**, spawn a fresh isolated subagent with the inverse brief:

> Given these artifacts (results, registry, locks) and this prose: (a) What is the *strongest* claim the evidence already supports that the text does not make? (b) Where does the text hedge below what the artifacts license (qualifiers, scope carve-outs, demotions with no triggered falsifier behind them)? (c) What is the cheapest single experiment that would license a materially stronger claim? Ground every finding in a named artifact; do not propose claims the evidence doesn't support.

Triage its findings like any review: strengthen the prose where licensed, queue the cheap experiment if its cost is justified, or log why not. Cross-check every flagged demotion against `REGISTRY.md` § Claim ledger — a demotion with no ledger row gets one now (with its forcing evidence) or gets reverted.

## §2 — Endgame (blind review of the complete artifact)

**Success bar (`BRIEF.md`):** the run succeeds when the blind-review score reaches **Weak Accept or higher** (internal §2a / §2c plus external §2b). Convergence *below* the bar (see the stopping rule) means *reviewing* is exhausted, not that the bar is met — a converged sub-Weak-Accept verdict is failure-to-meet-bar: it routes to the stuck/pivot fork (`playbooks/decisions.md`) while budget and time remain, and is shipped only when the budget or deadline is exhausted (honestly, with the gap stated in the completion report, `USER.md`). Never manufacture the score — reviewers are unauthorable, so the only path to the bar is genuinely meeting it.

### Run order

1. `scripts/gate_artifact.sh` passes (mechanical gates — see below).
2. Internal blind rounds (§2a) until the stopping rule fires.
3. External reviews (§2b) — this is a `PLAN.md` milestone, not an option.
4. One final internal blind round → its verdict is the accompanying final review.
5. Completion report to the operator (`USER.md`).

### Mechanical gates are scripts, never reviewer instructions

`scripts/gate_artifact.sh` checks, before every round: page budget; zero unresolved placeholders (`[CITE:`, `TODO`, `TKTK`, `% MISSING`); author-internal vocabulary greps (MAJOR, F-xx, "closure ledger", "untouchable", status/changelog blocks); deanonymization greps (agent name, repo/org, operator name); and the registered number-consistency checks. **Why:** left unguarded, these checks migrate into reviewer prompts as "floors" and "guards" until the review degenerates into a grep ritual. Reviewers grade; scripts verify. A reviewer must never be told about a floor, a guard, or a prior round's numbers.

### §2a — Internal blind rounds

**Reviewer isolation (both directions):**
- The reviewer subagent is **isolated** and receives only: the rendered PDF path + the spawn block below. It may not read any workspace file. Its output goes to `reviews/blind_round_<N>.md`.
- **The spawner-side rule — contamination enters through the spawner, not the reviewer:** when composing the spawn, you may not include round numbers, prior verdicts, prior findings, "do not re-raise" lists, streak context, or any expected outcome. Every round is round one from the reviewer's chair.

**Stopping rule (binding):**
- A round is **clean** iff it raises zero soundness- or claim-level findings (presentation-level findings get batched and fixed but do not reset the count).
- **Stop after 2 consecutive clean rounds, or at round 6, whichever is first.** Track rounds in `REGISTRY.md` § Review ledger (round, date, verdict, clean?). Spawning internal round 7 is a contract violation — proceed to §2b instead.
- **Central-claim rejection → fork (binding).** A round is a *central-claim rejection* iff its weaknesses name the headline claim itself as unsound / circular / tautological / vacuous — **not** presentation, and **not** "significance could be stronger" — and its recommendation is Weak Reject or below. Record it in the Review ledger (`central-claim hit?` column). When a central-claim rejection recurs across **≥2 rounds**, you may not spawn the next round: this is a mechanism-level failure (`playbooks/decisions.md` § Failure levels) — enter Stuck/Pivot, whose default is advancing the next portfolio candidate. Fixing presentation, adding datasets, or narrowing the claim does **not** clear it. **Tie-break:** a sound, brief-faithful artifact whose only remaining complaints are significance or presentation still ships the honest WEAK_ACCEPT at cap-6 — the fork is for vacuity/unsoundness of the *headline*, not for reviewers wanting more.
- **The bar gates shipping, not just convergence.** Stopping (2-clean / cap-6) ends *reviewing*; it does not authorize shipping a verdict **below Weak Accept** (the success bar — §2 intro, `BRIEF.md`). Below-bar at convergence → stuck/pivot fork while budget and time remain; ship below bar only when a cap is exhausted, disclosed in the completion report. (A Weak Accept is *at* the bar and ships.)
- If the verdict is identical two rounds running, further internal rounds are defined as zero-information: proceed to §2b regardless of clean-count.
- **Why:** blind-review verdicts plateau within ~5 rounds. Iterating past convergence produces contaminated reviewers and streak-gaming, never soundness. An honest WEAK_ACCEPT is a stronger ship-state than a manufactured ACCEPT.

**Triage:** as in §1 (you triage, reviewer grades). A finding DEFERred twice forces fix-or-document-as-limitation — no third defer.

**The reviewer spawn block** (verbatim; fill the slots; change nothing else):

```
You are a {{VENUE|NeurIPS}} reviewer. You have been assigned a paper to review.

Paper: <PATH-TO-PDF>

Your task: produce a {{VENUE|NeurIPS}}-style review using the official reviewer
guidelines (fetch them if helpful); the rubric below captures the structure.

Posture: HOSTILE-FAIR. Your job is to find reasons to recommend rejection,
to challenge every load-bearing claim, and to grade the paper as if you were
a real PC member who has read 8 other submissions this week. You are NOT the
author. Do not defend choices. Credit only what is in the PDF.

Output structure:
1. SUMMARY (3–5 sentences, in your words).
2. STRENGTHS (3–6 bullets).
3. WEAKNESSES (3–10 bullets), prioritising: (a) soundness — are claims
   supported by the evidence presented? (b) significance vs cited prior
   work; (c) novelty; (d) clarity on one cold read; (e) presentation —
   does this read as a finished submission or a converted internal
   document? Flag: leaked internal file paths, status/changelog blocks,
   author-internal vocabulary, section-numbering oddities, undefined
   symbols in the abstract, response-to-reviewer phrasings, deanonymising
   names, tables whose columns aggregate differently without a legend;
   (f) reproducibility.
4. QUESTIONS FOR THE AUTHORS (3–8).
5. LIMITATIONS (1–3, identified by you, not lifted from the paper's own).
6. SCORE (venue scale, integer + the rubric line you matched).
7. CONFIDENCE (venue scale, integer + rubric line).
8. RECOMMENDATION (Strong Accept / Accept / Weak Accept / Borderline /
   Weak Reject / Reject / Strong Reject).

Constraints:
- Read the PDF with pdftotext -layout, plus image inspection for figures
  that matter to your evaluation.
- Cite page numbers for every claim in your review.
- You may fetch the venue reviewer guidelines and cited papers' abstracts
  to check positioning claims. Do NOT search for the paper or its authors.
- You may NOT read any file in the workspace other than the PDF itself.
- Time budget: 90 minutes. Quality > speed.

Self-honesty check before declaring done: a page number for every
weakness; presentation graded explicitly; questions a real PC member
would ask, not a co-author.

Report back: the path to your written review.
```

### §2b — External reviews (a milestone, not an option)

Submit to every external reviewer in `TOOLS.md` § Accounts — **CMU Paper Reviewer** and **Stanford Agentic Reviewer** (browser-submit; the review returns **by email** to the `gog`-authenticated review Gmail, pulled with the `gog` CLI) and **refine.ink** (REST API via `REFINE_INK_API_KEY`) — respecting per-platform quotas and ordering: metered/paid platforms (likely refine.ink) go last, after the internal rounds are clean. The two portal→email reviewers are **asynchronous** — submit, then poll the inbox on a heartbeat cadence for the returned review rather than blocking on it. For each external review: triage findings exactly as §1, apply one fix pass, rebuild. **Why this is a gated milestone:** prose instructions to use external reviewers do not bind under deadline pressure; a milestone with a gate does. `gate_artifact.sh` for the final milestone checks that an external-review artifact exists in `reviews/external/`.

### §2c — The accompanying final review

After externals, one last internal blind round under §2a. Its full output ships with the deliverable as `reviews/final_review.md`, verdict **as the reviewer wrote it** — if it says Weak Reject, it ships saying Weak Reject. Add a ≤6-line author annotation at top: what was fixed in response to which round, and what you chose not to fix and why. Then send the completion report (`USER.md`).

## §3 — Brief-fidelity review (the question is the contract)

The endgame reviewers see only the PDF, so they structurally cannot catch the costliest failure: a polished deliverable that answers a different question than the one assigned. Scope drift happens in locally-reasonable steps, each licensed by honest evidence — no single step looks like drift from inside.

**At every milestone gate and once before the endgame:** spawn a fresh isolated subagent whose only inputs are `BRIEF.md` and the current draft (or `PLAN.md`, pre-draft). Brief:

> Read the brief, then the draft. (a) State the question the brief asks and the question the draft actually answers — are they the same? (b) Check every requirement and every "what not to do" in the brief against the draft; list violations. (c) Would the brief's author recognize this as what they asked for? Verdict: FAITHFUL / DRIFTING / DIVERGED, naming the single largest gap.

Triage: **DIVERGED, or any named-prohibition violation, forces the stuck/pivot procedure** (`playbooks/decisions.md`) — it may not be DEFERred or fixed with framing prose. **DRIFTING** gets checked against the Claim ledger and the `PLAN.md` portfolio: either the drift has forcing evidence (then it's a Tier-2 memo to re-scope, since it changes the contribution) or the work returns to the question.
