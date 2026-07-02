# Playbook: Review

Review serves the research cycle: `… ⇄ Draft ⇄ Review → Readability → Ship`. The default response to a real soundness finding is **return to the Experiment phase** — *what experiment closes this?* — which is a sanctioned back-edge, normal research, not a failure. Narrowing the claim is the *fallback*, used only when the closing experiment is genuinely out of budget. The honesty mechanism is a set of **isolated, unauthorable critics** (blind reviewers, power critic, brief-fidelity); their `reviews/*.md` verdicts are honored cooperatively — the ship gate reads the critic's own artifact, never an agent-typed certification string. The "why" notes are load-bearing — read them.

## §1 — Drafting reviews (hostile section review, during writing)

Use whenever a load-bearing section/version of the deliverable lands (intro, method, theory, experiments — anything other sections will cite).

- **Spawn a fresh isolated reviewer subagent** per version: input = the section file(s) + the claim-anchor files it must check against (locks, registry falsifiers, run artifacts). Brief: *find reasons to reject* — hunt specifically for (a) summary statistics not re-derived from artifacts, (b) claims that outrun their pre-registered falsifiers, (c) overclaim relative to the locked scope, (d) notation/definition drift against sibling sections, (e) narrative incoherence — prose that doesn't serve the claims/evidence/so-what frame or disagrees with `REGISTRY.md` § Claim & decision trail (`playbooks/writing.md`). Budget ~30–45 min. Output: `reviews/review_<section>_v<N>.md` with MAJOR/minor findings.
- **You triage** (the reviewer never does): SHIP (fix now — inline if <10 lines, revision subagent otherwise), DEFER (queue with a named owner-milestone), ESCALATE (Tier-2 memo if it reopens a locked scope decision).
- **Fixes must not accrete hedges.** When applying a fix, prefer deletion, restructuring, or a closing experiment over caveat-insertion; a fix whose only edit is a new qualifier sentence is a smell. Hedge-accretion across rounds is exactly how a draft rots into unreadability — each round's "honest" caveat compounds into prose no cold reader can follow (the §1b underclaim audit exists to reverse this; don't manufacture its work).
- **Cycle until the section is audit-stable:** review → fix → review. Two consecutive reviews with no MAJOR on a section = stable; move on. (Hostile section reviews catch confabulated statistics within hours of writing — the cheapest truth-enforcement in the harness. Do not skip them to save time.)

### §1b — Underclaim audit (the pro-strength counter-force)

Hostile reviews only push one direction: cut, demote, narrow. Run unopposed, they shrink the headline claim round after round until the deliverable is over-pruned — and a run that ends *weaker than its evidence licenses* is as much a failure as one that overclaims (`AGENTS.md`: symmetric claim discipline; the felt-budget heuristic — spend on depth, don't settle short). This is the active counter-force, not an afterthought. So, **once per drafting cycle on each load-bearing section, and once on the full draft before the endgame**, spawn a fresh isolated subagent with the inverse brief:

> Given these artifacts (results, registry falsifiers, locks) and this prose: (a) What is the *strongest* claim the evidence already supports that the text does not make? (b) Where does the text hedge below what the artifacts license (qualifiers, scope carve-outs, demotions with no triggered falsifier behind them)? (c) What is the cheapest single experiment that would license a materially stronger claim? Ground every finding in a named artifact; do not propose claims the evidence doesn't support.

Triage its findings like any review: strengthen the prose where licensed, **run the cheap strengthening experiment if its cost is justified** (this is the same "what experiment closes this?" default, pointed at strength rather than soundness — forward motion you owe, not grinding), or log why not. Cross-check every flagged demotion against `REGISTRY.md` § Claim & decision trail — a demotion with no trail row gets one now (with its forcing evidence) or gets reverted.

## §2 — Endgame (blind review of the complete artifact)

**Success bar (`BRIEF.md`):** the run succeeds when the blind-review recommendation reaches **Weak Accept or higher** (internal §2a / §2c plus external §2b). Convergence *below* the bar (see the stopping rule) means *reviewing* is exhausted, not that the bar is met — a converged sub-Weak-Accept verdict is failure-to-meet-bar: it routes to the stuck/pivot fork (`playbooks/decisions.md`) — default, return to the Experiment phase to close the gap — while budget and time remain, and is shipped only when the budget or deadline is exhausted (honestly, with the gap stated in the completion report, `USER.md`). Never manufacture the score — reviewers are unauthorable, so the only path to the bar is genuinely meeting it.

### Run order

1. `scripts/gate_artifact.sh` passes (mechanical form/hygiene gates — see below).
2. Internal blind rounds (§2a) until the yield-based stopping rule fires.
3. External reviews (§2b) — this is a `PLAN.md` milestone, not an option.
4. One final internal blind round → its verdict is the accompanying final review (§2c), written to `reviews/final_review.md`.
5. **Full-paper readability round** (`playbooks/writing.md` § The final readability round) — runs once §2 soundness review has converged and before ship. It is a **craft pass over the whole paper** (cold reads of every section by fresh isolated subagents, then one focused clarity/flow edit); it **does not reopen claims or re-run experiments** (a deep structural problem is logged for triage, not silently fixed at camera-ready). The procedure lives in `writing.md`; it sits *here* in the pipeline.
6. Completion report to the operator (`USER.md`).

The §2d power / evidence-adequacy critic runs at drafting entry (against the planned design) and again before ship (against the delivered artifacts); its ship-time verdict is what the evidence gate reads. It is not a sequential step in the run order so much as the gate on entering drafting and on shipping.

### Mechanical gates are scripts, never reviewer instructions

`scripts/gate_artifact.sh` checks, before every round: page budget; zero unresolved placeholders (`[CITE:`, `TODO`, `TKTK`, `% MISSING`); author-internal vocabulary greps (MAJOR, F-xx, "closure ledger", "untouchable", status/changelog blocks); deanonymization greps (agent name, repo/org, operator name); and leaked internal paths. **Why:** left unguarded, these checks migrate into reviewer prompts as "floors" and "guards" until the review degenerates into a grep ritual. Reviewers grade; scripts verify. A reviewer must never be told about a floor, a guard, or a prior round's numbers.

### §2a — Internal blind rounds

**Reviewer isolation (both directions):**
- The reviewer subagent is **isolated** and receives only: the rendered PDF path + the spawn block below. It may not read any workspace file. Its output goes to `reviews/blind_round_<N>.md`.
- **The spawner-side rule — contamination enters through the spawner, not the reviewer:** when composing the spawn, you may not include round numbers, prior verdicts, prior findings, "do not re-raise" lists, streak context, or any expected outcome. Every round is round one from the reviewer's chair.

**Stopping rule — yield-based (binding):**
- Stop when a round adds **no new soundness finding** — i.e. when the verdict and the set of soundness/claim-level weaknesses are unchanged from the prior round (**identical verdict two rounds running = done**). At that point further internal rounds carry zero information; proceed to §2b. (Presentation-level findings get batched and fixed but never keep the loop alive.)
- Keep a **sane upper bound** as a sanity heuristic, not a counter to game: blind-review verdicts plateau within ~5 rounds, so if you reach roughly half a dozen rounds without convergence, treat the *process* as the problem (contaminated reviewers, a moving artifact) and proceed to §2b rather than spawning more. There is no streak to track in any file.
- **Central-claim rejection → fork (binding).** A round is a *central-claim rejection* iff its weaknesses name the headline claim itself as unsound / circular / tautological / vacuous / **underpowered (rests on a single cell, too few seeds, or an interval consistent with the opposite verdict)** — **not** presentation, and **not** "significance could be stronger in the abstract" — and its recommendation is Weak Reject or below. When a central-claim rejection recurs across **≥2 rounds**, stop spawning rounds: this is a mechanism-level failure (`playbooks/decisions.md` § Failure levels) — enter Stuck/Pivot, whose default is **return to the Experiment phase** (run the experiment that would close it) or advance the next portfolio candidate. Fixing presentation, adding unrelated datasets, or narrowing the claim does **not** clear it. **Tie-break:** a sound, brief-faithful artifact whose only remaining complaints are significance or presentation still ships the honest WEAK_ACCEPT — the fork is for vacuity/unsoundness of the *headline*, not for reviewers wanting more. **The tie-break does NOT apply to evidence-adequacy complaints.** "The headline rests on one cell / one seed / an n that can't carry it" is *not* a significance nitpick — it is the evidence-adequacy failure the ship-time evidence gate (`REQUIRE_EVIDENCE_ADEQUATE`, via the §2d power critic) and the `BRIEF.md` success bar name; it routes to Stuck/Pivot or a powering experiment, never to "ship the honest WEAK_ACCEPT." Calling an n=1 headline a significance preference is the misclassification this clause exists to stop.
- **The bar gates shipping, not just convergence.** Reaching the yield-based stop ends *reviewing*; it does not authorize shipping a verdict **below Weak Accept** (the success bar — §2 intro, `BRIEF.md`). Below-bar at convergence → stuck/pivot fork (default: return to Experiment) while budget and time remain; ship below bar only when a cap is genuinely (near-)exhausted, disclosed in the completion report. This is **gate-checked, not self-judged**: the final gate runs `REQUIRE_SHIP_AUTHORIZATION=1` (`scripts/gate_artifact.sh`), a light backstop that reads `reviews/final_review.md` (Weak Accept+), `locks/budget.json` (a cap near-exhausted), or an honest ship/under-spend memo in `LOG.md`. (A Weak Accept is *at* the bar and ships.)
- **Why:** blind-review verdicts plateau within ~5 rounds. Iterating past convergence produces contaminated reviewers, never soundness. An honest WEAK_ACCEPT is a stronger ship-state than a manufactured ACCEPT.

**Triage:** as in §1 (you triage, reviewer grades). The default response to a real soundness finding is *what experiment closes this?* — return to the Experiment phase and run it. A finding DEFERred twice forces fix-or-document-as-limitation — no third defer.

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
8. RECOMMENDATION — write it on its own line beginning `Recommendation:`,
   one of: Strong Accept / Accept / Weak Accept / Borderline / Weak Reject /
   Reject / Strong Reject.

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

Submit to every external reviewer in `TOOLS.md` § Accounts — **CMU Paper Reviewer** and **Stanford Agentic Reviewer** (browser-submit; the review returns **by email** to the `gog`-authenticated review Gmail, pulled with the `gog` CLI) and **refine.ink** (REST API via `REFINE_INK_API_KEY`) — respecting per-platform quotas and ordering: metered/paid platforms (likely refine.ink) go last, after the internal rounds converge. The two portal→email reviewers are **asynchronous** — submit, then poll the inbox on a heartbeat cadence for the returned review rather than blocking on it. For each external review: triage findings exactly as §1 (default: *what experiment closes this?*), apply one fix pass, rebuild. **Why this is a gated milestone:** prose instructions to use external reviewers do not bind under deadline pressure; a milestone with a gate does. `gate_artifact.sh` for the final milestone checks (`REQUIRE_EXTERNAL_REVIEWS=1`) that an external-review artifact exists in `reviews/external/`.

**Synthesizing the slate — the over-index heuristic.** Do not let one favorable verdict outweigh the rest. The judgment to apply (you apply it; nothing in a file mechanizes it): **a lone at/above-bar review cannot overrule a soundness objection that ≥2 other reviews (internal §2c or external) raise on the *same axis*.** If reviewer A calls a design choice sound and reviewers B and C flag the *same* choice as a soundness problem, the slate is **below-bar on that axis**, not "mixed" — one positive vote does not rule the contested point settled. A genuinely **MIXED** slate requires the positive and negative verdicts to rest on *different* axes (e.g. accept on method, reject on scope), not one positive overruling same-axis negatives. When the majority — including the §2c accompanying final review — names the same central defect, that is **convergence below bar**: route to Stuck/Pivot (default, return to Experiment to close the defect); record the synthesis honestly in the completion report (do not headline the lone accept). The bar is met only when the *unauthorable* reviewers actually reached Weak Accept+, not when one did while others dissented on soundness.

### §2c — The accompanying final review

After externals, one last internal blind round under §2a. Its full output ships with the deliverable as `reviews/final_review.md`, verdict **as the reviewer wrote it** — if it says Weak Reject, it ships saying Weak Reject. Add a ≤6-line author annotation at top: what was fixed in response to which round, and what you chose not to fix and why.

The review **states its recommendation in plain words** — the reviewer's own line (e.g. `Recommendation: Weak Accept`) stays in `reviews/final_review.md` as written. There is no agent-typed certification to add. The ship gate (`REQUIRE_SHIP_AUTHORIZATION=1`, `scripts/gate_artifact.sh`) greps this file directly for a Weak Accept-or-higher recommendation; if it isn't there, the gate falls back to the budget lock or an honest ship-justification memo in `LOG.md` (it is a *light* backstop, not a hard cert check). Do not reword a Weak Reject into something the grep would mistake for accept — the file ships verbatim. Then run the readability round (run order step 5) and send the completion report (`USER.md`).

### §2d — Power / evidence-adequacy critic (the ship-time evidence gate's source)

The blind reviewers (§2a) grade the PDF and reliably flag "this rests on one cell," but the run can file that under "significance" and ship anyway. So evidence adequacy is judged not by self-assessment but by an **isolated power critic the agent cannot author** — same isolation discipline as the §2a blind reviewer and the exploration §4 critic. Spawn it at **drafting entry** (against the *planned* design, writing to `reviews/power_critic_drafting.md`) and again **before ship** (against the *delivered* artifacts, writing to `reviews/power_critic_ship.md`). The ship-time file `reviews/power_critic_ship.md` is what the `REQUIRE_EVIDENCE_ADEQUATE` gate reads directly — the critic's own artifact is the evidence, honored cooperatively; there is no certification line to author. The critic **enumerates the load-bearing numbers itself** from the headline claim and the delivered results and verifies each against its on-disk artifact; the spawner may not curate which it sees.

**Spawner-side rule (inherited from §2a):** the spawn may not include round numbers, prior verdicts, "we think this is powered," or any expected outcome. Every spawn is round one.

**The spawn block** (verbatim; fill the slots; change nothing else):

```
You are an external evaluation-methodology critic, posture HOSTILE-FAIR. Inputs:
the brief, the headline claim, and the run artifacts. You verify every
load-bearing number against its on-disk artifact. You are NOT the author. Do not
propose to ship. Do not grade presentation.

Brief:            <PATH-TO-BRIEF>
Headline claim:   <PATH or quote — base + stretch, from PLAN.md § Research plan>
Evidence floors:  locks/evidence_floors.json
Artifacts root:   <PATH(s)>

Answer, grounded only in the artifacts:
  (a) SINGLE-POINT TEST. What is the minimal set of observations the headline
      verdict actually rests on? Does removing any ONE observation (one cell,
      one seed, one prompt-set) flip or void the verdict? If yes → the headline
      is single-point.
  (b) FLOORS. How many seeds and how many load-bearing, non-degenerate cells
      support the headline? Compare to locks/evidence_floors.json. A negative/
      impossibility headline faces the SAME floor as a positive one.
  (c) INTERVAL TEST. For each load-bearing statistic, does its reported CI /
      error bar include a value consistent with the OPPOSITE conclusion? If the
      verdict rests on a point estimate whose interval straddles the threshold,
      say so.
  (d) SCALE CONSISTENCY. Are all legs of the headline run at the same scale
      (model size, trait/dataset breadth), or does a corroborating leg quietly
      run at a smaller scale than the headline claims?

Write your review to reviews/power_critic_<drafting|ship>.md. State explicitly,
on their own lines, the counts you found: "seeds=<N>" and "cells=<M>"
(load-bearing, non-degenerate cells). End with a single greppable line exactly:
"Verdict: ADEQUATE" (or "Verdict: UNDERPOWERED" / "Verdict: SINGLE-POINT" /
"Verdict: SCALE-INCONSISTENT"), naming the single cheapest experiment that would
close the gap. Cite the artifact for every claim. Time budget: 60 minutes.
Report back the path to your written review.
```

**Triage (you triage; the critic only grades):** the ship gate (`REQUIRE_EVIDENCE_ADEQUATE=1`) reads `reviews/power_critic_ship.md` directly — it must exist, carry `Verdict: ADEQUATE`, and report `seeds=<N>` ≥ `seed_floor` and `cells=<M>` ≥ `cell_floor` (`locks/evidence_floors.json`). A missing report, a non-ADEQUATE verdict, or counts below the floors fails the gate. The critic must state those counts as greppable lines; you cannot type them on its behalf.
- **ADEQUATE** with the counts meeting the floors → the headline is powered; drafting (at drafting entry) or shipping (at ship) proceeds.
- **UNDERPOWERED / SINGLE-POINT / SCALE-INCONSISTENT** → this is a Stuck/Pivot evidence-adequacy trigger (`playbooks/decisions.md`), **not** a limitations-section caveat and **not** dischargeable by the §2a tie-break. The default response is *what experiment closes this?* — run the named cheapest closing experiment (return to the Experiment phase; forward motion you owe, not grinding), or advance the next portfolio candidate. Re-spawn a *fresh* critic after. The headline does not enter drafting / does not ship until ADEQUATE.

## §3 — Brief-fidelity review (the question is the contract)

The endgame reviewers see only the PDF, so they structurally cannot catch the costliest failure: a polished deliverable that answers a different question than the one assigned. Scope drift happens in locally-reasonable steps, each licensed by honest evidence — no single step looks like drift from inside.

**At every milestone gate and once before the endgame:** spawn a fresh isolated subagent whose only inputs are `BRIEF.md` and the current draft (or `PLAN.md`, pre-draft). Brief:

> Read the brief, then the draft. (a) State the question the brief asks and the question the draft actually answers — are they the same? (b) Check every requirement and every "what not to do" in the brief against the draft; list violations. (c) Would the brief's author recognize this as what they asked for? Verdict: FAITHFUL / DRIFTING / DIVERGED, naming the single largest gap.

Triage: **DIVERGED, or any named-prohibition violation, forces the stuck/pivot procedure** (`playbooks/decisions.md`) — it may not be DEFERred or fixed with framing prose. **DRIFTING** gets checked against `REGISTRY.md` § Claim & decision trail and the `PLAN.md` portfolio: either the drift has forcing evidence (then it's a Tier-2 memo to re-scope, since it changes the contribution) or the work returns to the question.
