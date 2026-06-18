# Playbook: Exploration

Why this exists: the dominant front-of-run failure is racing to a deliverable — a one-pass literature skim and an hours-long candidate portfolio, then committing to the first idea that survives a single shallow scout. Exploration is the phase the rest of the scaffold is built to *race past* (there are detailed `writing.md` and `review.md` playbooks and none for exploration). On a multi-week horizon the agent should spend a substantial front fraction here, across **both literature and experimentation**, before any prose. This playbook is the authority the drafting-entry gate (`scripts/gate_artifact.sh` § exploration-adequacy), `PLAN.md`, and `AGENTS.md` point at.

**Forward action during exploration is exploration, not artifact progress.** A satisfying End-of-Turn LAUNCH/DELEGATE here is a launched scout, a dispatched lit-read subagent, or a newly registered candidate — *not* skeleton or prose work. "No idle" in this phase means more evidence, not faster drafting (`AGENTS.md` § The End-of-Turn Contract).

## §1 — The Exploration Dossier

The phase deliverable is `exploration/DOSSIER.md` (with `exploration/lit/` reading notes and `exploration/scouts/` verdict artifacts). Required sections — each a greppable header the gate checks structurally; the *substance* is enforced by the §4 critic, not the grep (reviewers grade, scripts verify):

- `## LIT SYNTHESIS` — a synthesis of the prior-work landscape, *not* a link dump: the sub-areas the brief's question touches, the strongest existing approach in each, and what each leaves open. Include a `### COVERAGE` note (the search surfaces actually worked — venues, keyword families, seed papers + forward/backward citation walks) and a `### NOVELTY` note (for each live candidate, the closest prior work and the one-sentence delta). Every cited claim obeys the evidence rules (`AGENTS.md` rule 1: artifact-or-it-didn't-happen — a fetched source file/URL + the quote) and the lit-gap rule. **A single hour-0 skim is not a lit synthesis.**
- `## HYPOTHESIS PORTFOLIO` — the `PLAN.md` candidate table, each candidate carrying a **scout verdict** in the standard bins (PASS / PARTIAL / FAIL / MALFORMED / AMBIGUOUS) against its pre-registered kill/promotion criterion, with the on-disk scout artifact path. This is the seam with the scout-depth rule (§3).
- `## DIRECTION CHOICE` — which candidate is promoted and why, grounded in the lit (novelty delta) and the scout evidence; the losers' rows kept current.
- `## OPEN CRUXES` — what lit + scouts did *not* resolve, framed as what the converge/draft phases must settle.

The promoted direction becomes the locked headline (`PLAN.md` § Current approach); it still faces the headline-substance certification at the drafting gate (§5).

## §2 — Literature-review procedure

Run lit review as fan-out, not a solo skim: one isolated subagent per sub-area (`playbooks/subagent.md`), each returning an Evidence Block; the parent spot-checks at least one claimed source per report (evidence rule 4). The `### COVERAGE` note must name what was actually searched, so the §4 critic can find unsearched regions. Lit review is a multi-pass activity that runs *alongside* scouting, not a one-time gate at hour 0.

## §3 — Scouting procedure (composes with scout-depth)

A candidate is **settled** only by a scout that returns a clean PASS or FAIL against its pre-registered criterion (`PLAN.md` § Candidate portfolio, `AGENTS.md` § Pre-registration verdict bins). An AMBIGUOUS, MALFORMED, or single-seed/under-powered scout does **not** settle a candidate — deepen or re-run it. Diagnose failures by level (`playbooks/decisions.md` § Failure levels) before deciding persist-vs-pivot.

**A clean FAIL counts as settled.** Killing an alternative is real exploration progress: a field where only one approach is live still reaches the breadth floor by *settling* (killing) the others, so the gate never stalls a genuinely single-viable-idea run.

## §4 — Exploration-sufficiency critic (the binding adversarial check)

Breadth that scales with the horizon is enforced not by a counter (counters manufacture fake scouts) but by an **isolated critic the agent cannot author** — the same isolation discipline as the §2a blind reviewer and the §3 brief-fidelity reviewer. **This critic, not any self-assessment, is what authorizes drafting.**

**Spawner-side rule (inherited verbatim from `review.md` §2a):** contamination enters through the spawner, not the reviewer. The spawn may not include round numbers, prior verdicts, "we think this is ready," "please confirm," or any expected outcome. Every spawn is round one. The critic **enumerates the scouts itself** from `## HYPOTHESIS PORTFOLIO` and the `REGISTRY.md` falsifier rows and cross-checks each against its on-disk artifact — the spawner may not curate which scouts it sees. A scout claim with no artifact on disk is fabricated.

**The spawn block** (verbatim; fill the slots; change nothing else):

```
You are an external research-program critic, posture HOSTILE-FAIR. Inputs: the
brief and an exploration dossier; you may also read the candidate portfolio table
and the registered falsifier rows, and you must verify each scout claim against
its on-disk artifact. Do NOT propose to start writing. You are NOT the author.

Paper brief: <PATH-TO-BRIEF>
Dossier:     <PATH-TO-DOSSIER>

Answer, grounded only in the artifacts:
  (a) Given the literature the dossier surveys, is the chosen direction the
      STRONGEST available — or is there a stronger / closer-to-frontier direction
      the exploration did not scout?
  (b) Is the lit coverage adequate to the brief's question, or are there obvious
      unsearched sub-areas / missing seminal work? Name them.
  (c) Did exploration STOP EARLY — is there a cheap, obvious next scout or
      candidate that would likely change the ranking?
  (d) Is the promoted direction backed by a CLEAN verdict (not AMBIGUOUS /
      single-seed), with the artifact present on disk?
  If only one direction is viable, say whether that is HONEST CONVERGENCE (the
  brief's space was genuinely searched and the alternatives were settled/killed)
  or PREMATURE NARROWING.

Verdict: ADEQUATE / STOP-EARLY / THIN-LIT / FABRICATED-OR-VACUOUS, naming the
single highest-value missing piece. Cite the dossier/artifact locations for every
claim in your review. Time budget: 60 minutes.
```

**Triage (you triage; the critic only grades):**
- **STOP-EARLY / THIN-LIT / FABRICATED-OR-VACUOUS** forces another exploration cycle on the *named* gap (execute the next scout, close the named lit gap, or replace a vacuous scout). It may not be deferred or papered over. Then re-spawn a *fresh* critic.
- **ADEQUATE** (including honest single-viable convergence) lets you write the certification line (§5).
- A STOP-EARLY/THIN-LIT recurring across **≥2 critic rounds with no new candidate surviving** is itself the honest-exhaustion signal → record an exhaustion memo (`playbooks/decisions.md` § Failure levels, question-level), and the critic affirms HONEST CONVERGENCE → ADEQUATE. This prevents infinite critic loops (mirrors review.md's "identical verdict two rounds = zero-information"). A persistent STOP-EARLY *with* a viable un-scouted candidate is instead a Stuck/Pivot trigger (`AGENTS.md` § Stuck tripwire) — scout it, don't draft around it.

## §5 — Phase exit and the drafting gate

Once the critic returns ADEQUATE, write exactly one line to `REGISTRY.md` § Exploration adequacy:

`EXPLORATION-ADEQUATE: settled-candidates=<N>; lit-coverage=yes; certified-by=<critic subagent / LOG ref>`

- `settled-candidates=N` with **N ≥ 2** (candidates settled by a clean PASS/FAIL), **or** `exhaustion-memo=<id>` for a critic-affirmed honest convergence.
- `lit-coverage=yes` only after an ADEQUATE (not THIN-LIT) verdict.

The drafting-entry gate (`REQUIRE_EXPLORATION_ADEQUATE=1 REQUIRE_HEADLINE_SUBSTANCE=1 scripts/gate_artifact.sh <pdf>`) reads this line. Prose drafting (`playbooks/writing.md` stage 1, the compressed-narrative lock) may not begin until that gate passes. The empty target-format skeleton (Milestone 1) is a compiling shell and does **not** authorize prose. Headline-substance (vacuous/by-construction) and exploration-adequacy are orthogonal and both gate drafting: substance asks *is the claim falsifiable*; adequacy asks *is this the strongest direction the lit and scouts support*.
