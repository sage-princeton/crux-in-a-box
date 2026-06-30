# Playbook: Exploration

Why this exists: the dominant front-of-run failure is racing to a deliverable — a one-pass literature skim and an hours-long candidate portfolio, then committing to the first idea that survives a single shallow scout. Exploration is the phase the rest of the scaffold is built to *race past*. On a multi-week horizon the agent should spend a substantial front fraction here, across **both literature and experimentation**, before any prose — but exploration is a *cycle*, not an hour-0 gate: `Set up → Explore ⇄ Experiment ⇄ Draft ⇄ Review` (`PLAN.md` § Research plan). Literature and scouting recur against open cruxes for as long as cruxes stay open. This playbook is the authority `PLAN.md` and `AGENTS.md` point at for what "explore deeply" means, and the home of the isolated exploration-sufficiency critic that authorizes drafting.

**Forward action during exploration is exploration, not artifact progress.** A satisfying End-of-Turn LAUNCH/DELEGATE here is a launched scout, a dispatched lit-read subagent, or a newly registered candidate — *not* skeleton or prose work. "No churning small work just to look busy" in this phase means more evidence, not faster drafting; a turn spent deepening exploration is a satisfying turn, not idle (`AGENTS.md` § The End-of-Turn Contract).

## §1 — The Exploration Dossier

The phase deliverable is `exploration/DOSSIER.md` (with `exploration/lit/` reading notes and `exploration/scouts/` verdict artifacts). Required sections — each a greppable header; the *substance* is graded by the §4 critic, not by a script (reviewers grade, scripts verify):

- `## LIT SYNTHESIS` — a synthesis of the prior-work landscape, *not* a link dump: the sub-areas the brief's question touches, the strongest existing approach in each, and what each leaves open. Include a `### COVERAGE` note (the search surfaces actually worked — venues, keyword families, seed papers + forward/backward citation walks) and a `### NOVELTY` note (for each live candidate, the closest prior work and the one-sentence delta). It names the **anchor papers** — the 3–5 closest to the contribution — each of which gets a full deep-read note (§2). Every cited claim obeys the evidence rules (`AGENTS.md`: artifact-or-it-didn't-happen — a fetched source file/URL + the quote). **A single hour-0 skim is not a lit synthesis.**
- `## HYPOTHESIS PORTFOLIO` — the `PLAN.md` candidate table, each candidate carrying a **scout verdict** in the standard bins (PASS / PARTIAL / FAIL / MALFORMED / AMBIGUOUS) against its pre-registered kill/promotion criterion, with the on-disk scout artifact path. This is the seam with the scout-depth rule (§3).
- `## DIRECTION CHOICE` — which candidate is promoted and why, grounded in the lit (novelty delta) and the scout evidence; the losers' rows kept current.
- `## OPEN CRUXES` — what lit + scouts did *not* resolve, framed as what the experiment/draft phases must settle. This is the standing target list: literature and scouting **recur against these cruxes** (§2), so the dossier stays live until they close, not until an hour-0 clock runs out.

The promoted direction becomes the locked headline (`PLAN.md` § Research plan). Drafting begins once the §4 sufficiency critic returns ADEQUATE (§5) — there is no self-typed certification to write.

## §2 — Literature: survey (fan-out) then deep-read

Literature is two distinct moves, and the second is the one most easily skipped.

**Survey (fan-out breadth).** Run the survey as fan-out, not a solo skim: one isolated subagent per sub-area (`playbooks/subagent.md`), each returning an Evidence Block; the parent spot-checks at least one claimed source per report (`AGENTS.md`: artifact-or-it-didn't-happen). The `### COVERAGE` note records what was actually searched, so the §4 critic can find unsearched regions. The survey's job is to map the landscape and **name the 3–5 anchor papers closest to the contribution**.

**Deep-read (depth on the anchors).** For each anchor paper named by the survey, read it **in full — methods and results, not the abstract** — and write a structured note at `exploration/lit/<paper>.md` with three parts:
- **What they did** — the actual method, setup, and scale (model size, sample/seed count, datasets), in enough detail to compare against this work's design.
- **Their actual numbers** — the load-bearing results as reported (with the table/figure they come from), not a paraphrase of the abstract's claim.
- **The limitation this work exploits** — the specific gap, untested regime, or weakness this contribution turns on. This is the novelty delta made concrete and the seed of a falsifier.

**Abstract-vs-read heuristic.** An abstract tells you a paper *exists* — not what they actually did, what their real numbers were, or where they were weak. Read the closest ones in full; skim the rest. A NOVELTY delta or a baseline comparison resting only on an abstract is unverified positioning, and the §4 critic treats a missing anchor deep-read as a coverage hole.

**Literature recurs against open cruxes.** Lit review is not an hour-0 gate that closes once. It runs *alongside* scouting and continues whenever `## OPEN CRUXES` names a question the current reading hasn't answered — a new candidate surfaces a new closest-prior-work, a reviewer cites a paper you haven't read, a crux needs the exact number from a method you only skimmed. Each such opening is a fresh deep-read note, not a re-skim.

## §3 — Scouting procedure (composes with scout-depth)

A candidate is **settled** only by a scout that returns a clean PASS or FAIL against its pre-registered criterion (`PLAN.md` § Candidate portfolio, `AGENTS.md` § Pre-registration). An AMBIGUOUS, MALFORMED, or single-seed/under-powered scout does **not** settle a candidate — deepen or re-run it. Diagnose failures by level (`playbooks/decisions.md` § Failure levels) before deciding persist-vs-pivot.

**A clean FAIL counts as settled.** Killing an alternative is real exploration progress: a field where only one approach is live still reaches the breadth floor by *settling* (killing) the others, so a genuinely single-viable-idea run is never stalled.

## §4 — Exploration-sufficiency critic (the binding adversarial check)

Breadth that scales with the horizon is enforced not by a counter (counters manufacture fake scouts) but by an **isolated critic the agent cannot author** — the same isolation discipline as the §2a blind reviewer and the §3 brief-fidelity reviewer in `review.md`. **This critic, not any self-assessment, is what authorizes drafting** — its `reviews/*.md` ADEQUATE verdict is honored cooperatively as the evidence that exploration is deep enough to write against; there is no separate certification line and no `gate_artifact.sh` flag to satisfy.

**Spawner-side rule (inherited from `review.md` §2a):** contamination enters through the spawner, not the reviewer. The spawn may not include round numbers, prior verdicts, "we think this is ready," "please confirm," or any expected outcome. Every spawn is round one. The critic **enumerates the scouts and anchor papers itself** from `## HYPOTHESIS PORTFOLIO`, the `## LIT SYNTHESIS` anchor list, and the `REGISTRY.md` § Falsifiers rows, and cross-checks each against its on-disk artifact — the spawner may not curate which it sees. A scout claim with no artifact on disk is fabricated; an anchor paper with no `exploration/lit/<paper>.md` deep-read note is an unread anchor.

**The spawn block** (verbatim; fill the slots; change nothing else):

```
You are an external research-program critic, posture HOSTILE-FAIR. Inputs: the
brief and an exploration dossier; you may also read the candidate portfolio table,
the deep-read notes under exploration/lit/, and the registered falsifier rows, and
you must verify each scout claim against its on-disk artifact. Do NOT propose to
start writing. You are NOT the author.

Paper brief: <PATH-TO-BRIEF>
Dossier:     <PATH-TO-DOSSIER>

Answer, grounded only in the artifacts:
  (a) Given the literature the dossier surveys, is the chosen direction the
      STRONGEST available — or is there a stronger / closer-to-frontier direction
      the exploration did not scout?
  (b) Is the lit coverage adequate to the brief's question, or are there obvious
      unsearched sub-areas / missing seminal work? Name them. For each anchor
      paper the synthesis names as closest to the contribution, is there a
      deep-read note (exploration/lit/<paper>.md) that states what they did, their
      actual numbers, and the limitation this work exploits — or is the positioning
      resting on an abstract?
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
- **STOP-EARLY / THIN-LIT / FABRICATED-OR-VACUOUS** forces another exploration cycle on the *named* gap (execute the next scout, close the named lit gap, deep-read the named unread anchor, or replace a vacuous scout). It may not be deferred or papered over. Then re-spawn a *fresh* critic.
- **ADEQUATE** (including honest single-viable convergence) authorizes drafting (§5). **But an ADEQUATE verdict certifies breadth, not power.** If the critic's verdict carries a deferred "highest-value remaining item" or any residual owed item about **scale / sample-count / seed-count / robustness of the result** (e.g. "scale/trait-count robustness of the negative"), that item is **not** punted to "the drafting phase" and **not** a certification to plumb — it is a **normal experiment to run**. Route it back to the Experiment phase: it becomes a `PLAN.md` work-queue row, pre-register its falsifier, and run it. This is a sanctioned back-edge of the research cycle (Explore → Experiment), not a failure. The §2d power critic in `review.md` re-checks at drafting entry and ship that the deferred power item was actually closed, against `locks/evidence_floors.json`. A deferred power item that nobody turns into a run is the exact failure that lets an underpowered headline reach drafting.
- A STOP-EARLY/THIN-LIT recurring across **≥2 critic rounds with no new candidate surviving** is itself the honest-exhaustion signal → record an exhaustion memo (`playbooks/decisions.md` § Failure levels, question-level), and the critic affirms HONEST CONVERGENCE → ADEQUATE. This prevents infinite critic loops (mirrors `review.md`'s "identical verdict two rounds = zero-information"). A persistent STOP-EARLY *with* a viable un-scouted candidate is instead a Stuck/Pivot trigger (`AGENTS.md` § Stuck tripwire) — scout it, don't draft around it.

## §5 — Phase exit and entering the draft

Once the §4 critic returns ADEQUATE, exploration is sufficient and prose drafting may begin (`playbooks/writing.md` stage 1, the compressed-narrative lock). The critic's `reviews/*.md` ADEQUATE verdict *is* the authorization — honored cooperatively, no line to type, no gate flag to flip. The empty target-format skeleton (Milestone 1) is a compiling shell and can exist earlier; it does **not** authorize prose, because the shell precedes exploration.

Breadth and depth are different questions and an ADEQUATE verdict answers only the first:
- **Breadth** (this section's critic) asks *is this the strongest direction the lit and scouts support — was the space genuinely searched, the anchors actually read?* Settling/killing candidates cheaply proves you *searched*; it never proves the surviving result is *strong*.
- **Power** (`playbooks/review.md` §2d, the isolated power critic, `locks/evidence_floors.json`) asks *is the headline powered enough to carry a contribution — not a single cell, meets the seed/cell floors?* A breadth-ADEQUATE run with an underpowered headline still fails the ship-time evidence gate. Any power item the §4 critic deferred routes to the Experiment phase as a real run (§4 triage), where the power critic verifies it closed.
