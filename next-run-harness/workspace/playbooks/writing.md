# Playbook: Writing the Paper

Canonical reference: **Neel Nanda, "Highly Opinionated Advice on How to Write ML Papers"** — https://www.alignmentforum.org/posts/eJGptPbbFPZGLpjsp/highly-opinionated-advice-on-how-to-write-ml-papers. **Fetch and read it in full before the first drafting milestone** (subagent task; distill any task-relevant additions back into this file). The summary below is the working protocol; the post is the authority where they differ.

## The frame: narrative = claims + evidence + why-it-matters

A paper is **1–3 specific, concrete claims** woven into one coherent narrative, supported by rigorous evidence, with explicit motivation. Everything in the paper either serves the narrative or doesn't belong in the main text. The claims are the `PLAN.md` headline claims (base + stretch) — the paper's narrative and the Claim ledger in `REGISTRY.md` must agree at all times.

Calibrate each claim's strength to its evidence: existence proof < systematic claim < guarantee. State which one you're making. (This is evidence rule 7 — symmetric claim discipline — applied to prose: don't overclaim, don't hedge below what the artifacts license.)

## Effort allocation (counterintuitive and load-bearing)

Readers spend ~30% of attention each on the **abstract**, the **introduction**, and the **figures** — and the remainder on everything else. Allocate writing and iteration effort in that proportion. A polished method section under a weak abstract is wasted work.

- **Abstract** (~5–6 sentences): uncontroversial context → main claim (1 sentence) → clarifying definition (1 sentence) → key evidence per claim (~1 each) → impact (1–2). No jargon that isn't field-standard.
- **Introduction**: an extended abstract — motivation, precise claims, key evidence, implications. End with a bullet contributions list: each claim + a pointer to its evidence.
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
5. **First full draft** in the target format (the skeleton already compiles — milestone 1).
6. **Iterative editing**: cut anything not serving the narrative.

Writing is not an afterthought at the end: budget a substantial trailing fraction of the run for distillation, and start stage 1 as soon as the evidence picture stabilizes.

## Failure modes to check yourself against

- **Illusion of transparency.** You have weeks of context; the reader has none. Define terms at first use, over-explain transitions, never reference internal artifacts or vocabulary (the gates check this mechanically).
- **Cherry-picking.** Present representative examples; mark exploratory vs pre-specified analyses (the registry's pre-registered/post-hoc distinction maps directly onto this); don't present an existence proof as a systematic claim.
- **Weak baselines.** Effort spent polishing your method while baselines run at defaults is a rigged comparison — reviewers price it as such. Baseline tuning effort is part of the claim's evidence.
- **Complexity as a virtue.** The strongest papers apply simple techniques carefully. Simplify the method before defending the complexity.
- **Unverified evidence in prose.** Before paper-writing mode, verify the load-bearing results (the evidence rules and the gate's number sweep already enforce this; the writing-stage addition is: don't begin stage 5 while any headline number lacks its registry row).
- **Statistical looseness.** Pre-specify confirmatory tests; never promote an exploratory result to a confirmatory claim.

Reproducibility ships with the paper: a fresh-machine README and the one-command repro for headline numbers are milestone gates, not camera-ready chores.
