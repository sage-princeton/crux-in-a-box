# Playbook: Writing the Paper

Canonical reference: **Neel Nanda, "Highly Opinionated Advice on How to Write ML Papers"** — https://www.alignmentforum.org/posts/eJGptPbbFPZGLpjsp/highly-opinionated-advice-on-how-to-write-ml-papers. **Fetch and read it in full before the first drafting milestone** (subagent task; distill any task-relevant additions back into this file). The summary below is the working protocol; the post is the authority where they differ.

## The frame: narrative = claims + evidence + why-it-matters

A paper is **1–3 specific, concrete claims** woven into one coherent narrative, supported by rigorous evidence, with explicit motivation. Everything in the paper either serves the narrative or doesn't belong in the main text. The claims are the `PLAN.md` headline claims (base + stretch) — the paper's narrative and the Claim ledger in `REGISTRY.md` must agree at all times.

Calibrate each claim's strength to its evidence: existence proof < systematic claim < guarantee. State which one you're making. (This is evidence rule 7 — symmetric claim discipline — applied to prose: don't overclaim, don't hedge below what the artifacts license.)

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
3. **Bullet outline of the full paper** — every section has a named job for a skeptical reader.
4. **Results and figures** — do the experiments actually support the narrative? What's missing is now visible cheaply; queue the gap experiments before writing prose around them.
5. **First full draft** in the target format (the skeleton — built from the venue template `templates/paper_template.zip`, neurips_2026.tex/.sty — already compiles, milestone 1).
6. **Iterative editing**: cut anything not serving the narrative.

Writing is not an afterthought at the end: budget a substantial trailing fraction of the run for distillation, and start stage 1 as soon as the evidence picture stabilizes. **Stage 1 may not begin until the drafting-entry gate passes** — exploration critic-certified adequate and the headline substance-certified (`REQUIRE_EXPLORATION_ADEQUATE=1 REQUIRE_HEADLINE_SUBSTANCE=1 scripts/gate_artifact.sh <pdf>`; see `playbooks/exploration.md` §5). "The evidence picture stabilizes" is exactly that gate, not a vibe: exploration is a multi-pass, multi-day phase on a long horizon, not a hour-0 skim.

## Failure modes to check yourself against

- **Illusion of transparency.** You have weeks of context; the reader has none. Define terms at first use, over-explain transitions, never reference internal artifacts or vocabulary (the gates check this mechanically).
- **Cherry-picking.** Present representative examples; mark exploratory vs pre-specified analyses (the registry's pre-registered/post-hoc distinction maps directly onto this); don't present an existence proof as a systematic claim.
- **Weak baselines.** Effort spent polishing your method while baselines run at defaults is a rigged comparison — reviewers price it as such. Baseline tuning effort is part of the claim's evidence.
- **Complexity as a virtue.** The strongest papers apply simple techniques carefully. Simplify the method before defending the complexity.
- **Unverified evidence in prose.** Before paper-writing mode, verify the load-bearing results (the evidence rules and the gate's number sweep already enforce this; the writing-stage addition is: don't begin stage 5 while any headline number lacks its registry row).
- **Statistical looseness.** Pre-specify confirmatory tests; never promote an exploratory result to a confirmatory claim.

Reproducibility ships with the paper: a fresh-machine README and the one-command repro for headline numbers are milestone gates, not camera-ready chores.

## The final readability pass (abstract + intro + Figure 1, once, at the end)

Why: the costliest readability failures are front-loaded — a cold reader who can't get the contribution from the abstract and introduction stops there. Reader attention concentrates on exactly the front matter (effort allocation above), and the illusion of transparency is worst where you've reread your own words most. So once the artifact is audit-stable (the endgame blind rounds have converged, `playbooks/review.md` §2), run **one** dedicated readability round on the abstract, introduction, and Figure 1 — and nothing else.

- **Cold-read first, isolated (you cannot run this on yourself).** Spawn a fresh isolated subagent given *only the rendered PDF*, briefed: "Reading only the abstract and introduction, tell me (a) the subfield and type of paper, (b) the main claim and its stated standard of evidence, (c) the key evidence, (d) why it matters. Then flag every place a cold reader is confused, every undefined term, and whether the abstract leads with the contribution and fits its budget — it must not spill onto a second page." The gap between what the cold-reader extracts and what you intended *is* the edit list.
- **One focused edit** in response: tighten the abstract to the sentence-roles above; make the introduction's contribution and contributions-list legible on a single read; fix Figure 1's takeaway and caption (`playbooks/figures.md`).
- **Single round, terminal, narrow.** This is a craft pass on the front matter, distinct from the soundness-focused blind review (§2a): it does not reopen claims, re-run experiments, or loop. It runs at the camera-ready milestone, after §2 has converged and before the deliverable ships.
