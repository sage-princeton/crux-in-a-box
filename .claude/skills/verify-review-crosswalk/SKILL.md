---
name: verify-review-crosswalk
description: Curate and mechanically verify the expert-vs-self-review crosswalk table (each expert core criticism paired with a verbatim quote from the agent's final self-review). Use when building or editing a review-crosswalk figure.
---

# Verify the review crosswalk

The **crosswalk table** pairs each human expert's core criticisms with the
closest concern in the agent's final self-review. Picking the pairs is
judgment; the quotes must nevertheless be **verbatim**, and that part is
checked mechanically.

## Curation procedure

1. From the expert review, extract 3–5 **core criticisms** (the reviewer's
   load-bearing objections, usually one per weakness theme). Give each a
   short editorial label ("Unprincipled trait selection") and a brief
   verbatim quote fragment.
2. From the agent's final self-review (`final_review.md` / `final_blind.md`
   in the run workspace), find the closest matching concern and take a brief
   verbatim fragment. Use "…" for elisions; never paraphrase inside quotes.
3. Record the pairs in `app/data/reviewCrosswalk.ts`.

## Mechanical verification

```
python3 .claude/skills/verify-review-crosswalk/verify.py \
  --data app/data/reviewCrosswalk.ts \
  --source <slug>:expert=<expert-review.md> \
  --source <slug>:self=<final-self-review.md>
```

Every fragment between elisions must appear verbatim in its source
(typography-normalized: curly/straight quotes, dashes, markdown emphasis,
"et. al."/"et al." are treated as equal). Any miss fails the check.

## Verify

`python3 .claude/skills/verify-review-crosswalk/test.py` — verifies the
published CRUX-2 crosswalk against both run repos' final self-reviews and
the expert reviews in the article appendix (23 fragments).
