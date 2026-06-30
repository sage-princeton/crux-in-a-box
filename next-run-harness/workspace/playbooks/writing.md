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

Writing is not an afterthought at the end: budget a substantial trailing fraction of the run for distillation, and start stage 1 as soon as the evidence picture stabilizes. **Stage 1 (the compressed-narrative lock) may not begin until the exploration-sufficiency critic returns ADEQUATE** against the dossier (`playbooks/exploration.md`). That isolated, unauthorable critic is honored cooperatively — its `reviews/*.md` ADEQUATE verdict is what authorizes drafting; there is no self-typed certification line and no drafting-entry gate flag to clear. "The evidence picture stabilizes" means exactly that critic verdict, not a vibe: exploration is a multi-pass, multi-day phase on a long horizon, not an hour-0 skim. (The empty target-format skeleton, Milestone 1, is a compiling shell and does not count as prose.)

## Failure modes to check yourself against

- **Illusion of transparency.** You have weeks of context; the reader has none. Define terms at first use, over-explain transitions, never reference internal artifacts or vocabulary (the gates check this mechanically).
- **Cherry-picking.** Present representative examples; mark exploratory vs pre-specified analyses (a pre-specified test is one whose falsifier was declared before the experiment — `REGISTRY.md` § Falsifiers; an exploratory finding has no such row and must be labelled as such); don't present an existence proof as a systematic claim.
- **Weak baselines.** Effort spent polishing your method while baselines run at defaults is a rigged comparison — reviewers price it as such. Baseline tuning effort is part of the claim's evidence.
- **Complexity as a virtue.** The strongest papers apply simple techniques carefully. Simplify the method before defending the complexity.
- **Unverified evidence in prose.** Before paper-writing mode, verify the load-bearing results (`AGENTS.md` evidence heuristic 1, *artifact-or-it-didn't-happen* — re-derive each headline number **once, when it is first promoted to a claim**, not every round). The writing-stage addition: don't begin stage 5 while any headline number is still unbacked by the artifact it claims.
- **Statistical looseness.** Pre-specify confirmatory tests; never promote an exploratory result to a confirmatory claim.

Reproducibility ships with the paper: a fresh-machine README and the one-command repro for headline numbers are milestone gates, not camera-ready chores.

## The final readability round (whole paper, once, terminal)

A **dedicated terminal milestone** after the §2 soundness review has converged and before Ship (`playbooks/review.md` §2; `PLAN.md`'s Readability milestone) — distinct from the soundness round, and a full-paper round, not a front-matter-only pass. Soundness review asks *is the paper right?*; this round asks *is the paper legible on one cold read, end to end?*

Why a whole-paper round and not just the front matter: yes, reader attention concentrates on the abstract, intro, and figures (effort allocation above), and the costliest single failure is a cold reader who can't get the contribution and stops there. But a paper that loses the reader in a muddy method section, an unlabelled table, or a results paragraph whose job isn't clear has the same fate one section deeper — and the illusion of transparency is worst everywhere you've reread your own words most, not only up front. So this round cold-reads **every section**, and the illusion is breakable only by a reader who has none of your context.

- **Cold-read every section, isolated (you cannot run this on yourself).** Spawn fresh isolated subagents given *only the rendered PDF*, covering the **whole paper** — abstract, introduction, every method/results/discussion section, the figures, captions, and tables (fan out across sections or assign a section each; assume zero ambient context, `playbooks/subagent.md`). Each is briefed to report, for its assigned sections: (a) what a cold reader takes away — for the front matter specifically, the subfield and paper type, the main claim and its stated standard of evidence, the key evidence, and why it matters; (b) every place a cold reader is confused, every undefined term, every place the flow breaks; (c) whether each section's *job* is legible on a single read (does the section do the thing its name promises, and can the reader tell?). The gap between what the cold-readers extract and what you intended *is* the edit list. (Front-matter-specific checks still apply: the abstract must lead with the contribution and fit its budget — it must not spill onto a second page.)
- **Structure from exemplars (informs the edit, not the cold-read).** The **`.tex` sources of the best published anchor papers** are already on disk in `paper/exemplars/` (fetched during exploration, `playbooks/exploration.md` §2) — use them as the structural templates. If the closest prior work isn't also the best-*written* in the area, pull 1–2 additional well-structured accepted papers (from `exploration/DOSSIER.md` § LIT SYNTHESIS or the target venue) alongside them. Match their *structural shape* — how they open the abstract, sequence the introduction's arc, phrase the contributions list, frame Figure 1, and order and proportion the body sections — never their content.
- **One focused edit pass across the whole paper**, in response to the cold-reads and guided by those exemplars: clarity, flow, and legibility everywhere — tighten the abstract to the sentence-roles above; make the introduction's contribution and contributions-list legible on a single read; fix each section so its job lands on one read; fix figure takeaways, captions, and tables (`playbooks/figures.md`). It **does not reopen claims or re-run experiments** — if a cold-read surfaces a *deep* structural problem (organization, not polish) or anything that would change a claim, log it as a finding for triage (`playbooks/decisions.md`); do not silently restructure or patch a blind-reviewed artifact at camera-ready.
- **Single round, terminal, bounded.** This is a craft pass over the whole paper, distinct from the soundness-focused blind review (§2a): it runs **once**, after §2 has converged and before the deliverable ships, and it is not a loop.
