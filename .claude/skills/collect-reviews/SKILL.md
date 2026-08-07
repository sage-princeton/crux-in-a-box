---
name: collect-reviews
description: Collect the review-trajectory data (strengths/weaknesses counts + verdicts for every self-review and external AI review) from a CRUX run workspace's reviews/ directory. Use when building or refreshing a crux2-*-reviews figure for a new run.
---

# Collect review-trajectory data

Produces the data behind the **review-trajectories figure**: one row per
review (self-reviews chronological, then external AI systems, then the human
expert), with strengths/weaknesses counts and the verdict.

## Steps

1. Deterministic first pass:

   ```
   python3 .claude/skills/collect-reviews/collect.py --reviews-dir <run>/reviews
   ```

   It discovers `blind_round_*.md`, `final_review.md`, `final_blind.md` and
   `external/*.md`; extracts score/10, confidence/5, and the verdict label;
   and counts items under the Strengths / Weaknesses / Limitations headings
   (lettered `**(a)`, `W1`-numbered, `- ` bullets, bold-led blocks — it
   reports both a primary count and `n_weaknesses_max` across patterns).

2. Judgment pass — apply these counting rules before publishing (they are the
   rules used for CRUX 2; keep a per-row `notes` column recording each call):
   - Count top-level **criticisms**, not rubric-axis headers: when weaknesses
     are grouped under axis headers, count the items, not the headers.
   - Author-questions and question-section items are **not** weaknesses.
   - Positive bullets inside a weaknesses section are **not** counted.
   - Nested presentation sub-flags under one criticism count **once**.
   - External formats: refine.ink = its scored comments (`### N.`), CMU = its
     `## Item N` blocks (neither has strengths sections — record
     strengths=null so the chart renders a dash); Stanford digests need
     manual reading (its "main reservations" are the weaknesses).
   - Omit stale reviews of outdated drafts (note why).
   - A review the agent never obtained (e.g. an unused refine.ink credit) is
     a row with a `note`, not a missing row.
3. Save the audited output as `visualizations/crux-2-data/<run>_reviews.csv`
   (schema: review_type, file, extracted_at_utc, in_file_timestamp,
   n_strengths, n_weaknesses, n_limitations, verdict, notes) and transcribe
   into the `ReviewGroup[]` in `app/data/plots.ts`. The human-expert row
   comes from the author review (see the collect-expert-ratings skill).

## Verify

`python3 .claude/skills/collect-reviews/test.py` — runs the collector on
both CRUX-2 run repos and checks it against the hand-audited CSVs: verdicts,
scores, confidences, and strengths must match exactly; weakness counts must
match exactly for most rows, with the documented-judgment remainder printed.
