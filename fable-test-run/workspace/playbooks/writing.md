# Playbook: Writing the Paper

Canonical reference: **Neel Nanda, "Highly Opinionated Advice on How to Write ML Papers"** — https://www.alignmentforum.org/posts/eJGptPbbFPZGLpjsp/highly-opinionated-advice-on-how-to-write-ml-papers. **Fetch and read it in full before the first drafting milestone** (subagent task; distill any task-relevant additions back into this file). The summary below is the working protocol; the post is the authority where they differ.

## The frame: narrative = claims + evidence + why-it-matters

A paper is **1–3 specific, concrete claims** woven into one coherent narrative, supported by rigorous evidence, with explicit motivation. Everything in the paper either serves the narrative or doesn't belong in the main text. The claims are the `PLAN.md` headline claims (base + stretch) — the paper's narrative and `REGISTRY.md` § Claim & decision trail must agree at all times.

Calibrate each claim's strength to its evidence: existence proof < systematic claim < guarantee. State which one you're making. (This is **symmetric claim discipline** — the third of `AGENTS.md`'s evidence heuristics — applied to prose: don't overclaim, don't hedge below what the artifacts license.)

## Effort allocation (counterintuitive and load-bearing)

Readers spend ~30% of attention each on the **abstract**, the **introduction**, and the **figures** — and the remainder on everything else. Allocate writing and iteration effort in that proportion. A polished method section under a weak abstract is wasted work.

- **Abstract** (~5–6 sentences; highest-polish object in the paper — most readers read only this and leave). One idea per sentence; simplest language that stays precise; no jargon that isn't field-standard (define the unavoidable terms). It must situate a cold-start reader who has none of your context — counter the illusion of transparency. Sentence roles, in order:
  1. **Context** — one uncontroversially-true sentence that locates the subfield ("Thinking models are now state-of-the-art across many reasoning tasks").
  2. **Need** — the gap, unknown, or problem this paper addresses; conveys the motivation.
  3. **Contribution** — the crucial claim and why it's exciting. Losing nuance here is fine.
  4. *(optional)* **Clarification** — what the claim means / how it's evidenced, if not obvious.
  5+. **Evidence** — one sentence per key result or supporting claim; fold a concrete metric into the sentence so the result reads as real and substantial.
  - **Close** (1–2 sentences) — why it matters, and state the **standard of evidence** explicitly ("a preliminary step toward…", "compelling evidence that…", "practitioners should take care when using Y", "establishes best practices for Z").
- **Introduction**: an extended abstract — more depth, room to define the technical terms the claims require. Cite liberally, but for *context the reader needs*, not performative coverage: aim for ≥1 citation per step of an important argument (the field is real, the problem matters, prior attempts are inadequate). Default paragraph structure:
  1. **Context** — topic, key motivating question, why it matters (optionally one sentence on how you answer it).
  2. **Technical background** — what's known, the established techniques you rest on, and why prior work is inadequate; situate the problem in the field's strategic picture.
  3. **Contribution** — the main claim, restoring the nuance/detail the abstract dropped (one more paragraph per additional claim).
  3.5. **The case** — the most critical evidence that the claim holds.
  4. **Impact** — the takeaway and implications: who should do something differently, what misconception is corrected, what science is advanced.
  - **Contributions list** — end with a bullet list: each claim + a concise pointer to its evidence. A reader should be able to skim it and decide if they're interested.
- **Figure 1**: design it around "what exactly should the reader take away?" Annotate, emphasize the key comparison, caption with context + interpretation + technical detail. All figure/diagram/formatting craft lives in `playbooks/figures.md` — spec, style canon, and the render-and-look loop.
- **Related work**: clarifies novelty against prior work; penultimate section unless the contribution is literature-heavy.
- **Discussion/limitations**: stated limitations build credibility; hidden ones get found by reviewers and cost more.
- **Appendices**: everything true-but-off-narrative, plus a tacit-knowledge appendix if useful (negative results, debugging lessons, hyperparameter intuitions).

## Process: compress first, expand iteratively

Never start at the first draft. Each stage gets a review pass (hostile + underclaim, `playbooks/review.md` §1/§1b) before expanding to the next:

1. **Compressed narrative** (half a page): the 1–3 claims, why each matters, the key experiment behind each. This document is the paper's lock — write it before any prose and check it against `REGISTRY.md`.
2. **Bullet outline of the introduction** (claims, novelty, stakes).
3. **Bullet outline of the full paper** — every section has a named job for a skeptical reader. Compare the section plan against the **arXiv `.tex` sources of the best published anchor papers** (fetched into `paper/exemplars/` during exploration, `playbooks/exploration.md` §2) and any other well-structured exemplar from the lit synthesis or the target venue — match how strong papers in the area sequence the argument.
4. **Results and figures** — do the experiments actually support the narrative? What's missing is now visible cheaply; queue the gap experiments before writing prose around them.
5. **First full draft** in the target format (the skeleton — built from the venue template `templates/paper_template.zip`, neurips_2026.tex/.sty — already compiles, milestone 1).
6. **Iterative editing**: cut anything not serving the narrative.

**First-full-draft cold read (early, once).** The moment stage 5 first yields a complete draft, spawn **one isolated cold-reader** — rendered PDF only, zero ambient context (`playbooks/subagent.md`) — briefed to report: what is the contribution and its stated standard of evidence? where did a cold reader get lost? which sections' jobs aren't legible on one read? This is the early-warning twin of the terminal readability round below: it runs while the Draft ⇄ Experiment back-edge is still open, so a structural legibility problem costs an edit here but only a triage memo there. Clarity findings only — soundness belongs to the §1/§2 reviews (`playbooks/review.md`); do not let this reader grade claims.

Writing is not an afterthought at the end: budget a substantial trailing fraction of the run for distillation, and start stage 1 as soon as the evidence picture stabilizes. **Stage 1 (the compressed-narrative lock) may not begin until the exploration-sufficiency critic returns ADEQUATE** against the dossier (`playbooks/exploration.md`). That isolated, unauthorable critic is honored cooperatively — its `reviews/*.md` ADEQUATE verdict is what authorizes drafting; there is no self-typed certification line and no drafting-entry gate flag to clear. "The evidence picture stabilizes" means exactly that critic verdict, not a vibe: exploration is a multi-pass, multi-day phase on a long horizon, not an hour-0 skim. (The empty target-format skeleton, Milestone 1, is a compiling shell and does not count as prose.)

## Failure modes to check yourself against

- **Illusion of transparency.** You have weeks of context; the reader has none. Define terms at first use, over-explain transitions, never reference internal artifacts or vocabulary (the gates check this mechanically).
- **Cherry-picking.** Present representative examples; mark exploratory vs pre-specified analyses (a pre-specified test is one whose falsifier was declared before the experiment — `REGISTRY.md` § Falsifiers; an exploratory finding has no such row and must be labelled as such); don't present an existence proof as a systematic claim.
- **Weak baselines.** Effort spent polishing your method while baselines run at defaults is a rigged comparison — reviewers price it as such. Baseline tuning effort is part of the claim's evidence.
- **Complexity as a virtue.** The strongest papers apply simple techniques carefully. Simplify the method before defending the complexity.
- **Unverified evidence in prose.** Before paper-writing mode, verify the load-bearing results (`AGENTS.md` evidence heuristic 1, *artifact-or-it-didn't-happen* — re-derive each headline number **once, when it is first promoted to a claim**, not every round). The writing-stage addition: don't begin stage 5 while any headline number is still unbacked by the artifact it claims.
- **Statistical looseness.** Pre-specify confirmatory tests; never promote an exploratory result to a confirmatory claim.

Reproducibility ships with the paper: a fresh-machine README and the one-command repro for headline numbers are milestone gates, not camera-ready chores.

## The Presentation Overhaul (mandatory terminal milestone, acceptance-tested)

A **mandatory terminal milestone** after the §2 soundness review has converged and before Ship (`playbooks/review.md` §2; `PLAN.md`'s Presentation Overhaul milestone). Soundness review asks *is the paper right?*; this milestone asks *is the paper legible to a cold expert reader, end to end?* — and it does not pass on your judgment of that, only on the acceptance test below.

Why this exists: revision rounds rot prose. Each review fix adds a hedge, a qualifier, an internal term; none looks harmful alone; the accumulated result is an over-long abstract, ALL-CAPS emphasis, jargon-saturated sections, and figures that never got built — a paper a reviewer stops reading on page 1. The illusion of transparency is worst everywhere you've reread your own words most. By this point in the run you are structurally the wrong judge of your own prose, which is why the exit condition belongs to cold readers and a gate script, not to you.

**The one frozen thing is the science.** Unlike earlier drafting passes, **restructuring is authorized**: reorganize sections, rewrite any prose, redesign the narrative flow, rebuild figures. What is *not* authorized: changing any claim's content or strength, any number, or any evidence. After the overhaul, verify the prose still matches `REGISTRY.md` § Claim & decision trail exactly and re-run the brief-fidelity check (`playbooks/review.md` §3); log anything borderline. No new experiments — figures are built from cached artifacts (`playbooks/figures.md`).

The overhaul requirements:

1. **Abstract to the sentence-role spec** (§ Effort allocation above): 5–6 sentences, hard cap {{ABSTRACT_WORD_CAP|200}} words, simplest language that stays precise. Losing nuance here is fine.
2. **Vocabulary**: zero ALL-CAPS emphasis in prose; zero author-internal vocabulary; every technical term defined at first use, judged from the cold reader's chair.
3. **Figures in the main body**: at minimum a Figure 1 designed around "what exactly should the reader take away," rendered and inspected at final size (`playbooks/figures.md`).
4. **De-hedging sweep**: where qualifiers accreted across review rounds, restate each claim once, cleanly, at exactly the strength the registry licenses — cut the scar tissue without changing the claim.
5. **Structure from exemplars**: match the structural shape of the anchor papers' `.tex` sources in `paper/exemplars/` (abstract opening, introduction arc, contributions list, Figure 1 framing, section proportions) — never their content. If the closest prior work isn't the best-*written*, pull 1–2 additional well-structured accepted papers from the dossier or venue.

**The acceptance test (binding — the milestone gate):** the overhaul is done when BOTH hold, not when you feel done:

- `REQUIRE_PRESENTATION=1 scripts/gate_artifact.sh <pdf>` passes (abstract length, ALL-CAPS, figure-in-body, plus the standing form gates); and
- **two fresh isolated cold-readers**, spawned per `playbooks/subagent.md` with *only the rendered PDF* and zero ambient context, each correctly state the main claim, its standard of evidence, and why it matters — and report no undefined terms and no section whose job they couldn't follow on one read. Spawner-side contamination rules apply (`playbooks/review.md` §2a): no prior verdicts, no "we think it's ready."

Iterate cold-read → edit until the test passes. If it hasn't passed after **three rounds**, the process is the problem — write a Tier-2 memo diagnosing why (usually: the paper needs a structural change you've been avoiding, or a claim is genuinely illegible because it is genuinely unclear) and act on it; do not ship past a failing acceptance test, and do not soften the test.

## The final README (the run's last commit)

After the Presentation Overhaul passes and before the completion report: write (or rewrite) the repo-root `README.md` as the **front door for a cold visitor**, and commit it as the run's final commit (locally — this run has no git remote). The repo outlives the run; a visitor landing on it — an evaluator, a researcher, the operator months later — gets the README first and decides in two minutes whether anything here is worth their time. Write for that reader: zero project context, zero harness context.

Contents, in order:

1. **What this is and what it found** — two or three plain-language sentences: the research question, the headline finding at exactly the strength `REGISTRY.md` licenses (the README is documentation, not a new claims surface), and why it matters.
2. **The paper** — path to the final PDF and its source.
3. **Repo map** — a short annotated list of the directories that matter (code, data, results, reviews), each with one plain clause on what's in it. Not a full tree; the five things a visitor needs.
4. **Reproduce the headline result** — environment setup and the one-command repro (this command must actually work from a fresh clone; it is the same reproducibility obligation as § above, now surfaced where a visitor will find it).
5. **Data** — one line per dataset: what it is, where it came from or how it was generated.

Style rules are the Presentation Overhaul's: no author-internal vocabulary, no ALL-CAPS emphasis, every technical term defined at first use, and short — the whole file legible in ~2 minutes.

**Acceptance:** `REQUIRE_README=1 scripts/gate_artifact.sh <pdf>` passes, and one fresh isolated cold-reader given *only* `README.md` can answer: what did this project find, where is the paper, and how would I rerun the headline result. Then commit and send the completion report.
