---
name: collect-expert-ratings
description: Extract the criterion scores (Quality/Clarity/Significance/Originality, overall 1-6, confidence 1-5) from an author's NeurIPS-template expert review. Use when building the expert-ratings table for a new shadow evaluation.
---

# Collect expert ratings

Produces the data behind the **expert-ratings table** (the dot-score table
summarizing each original author's review of the agent's paper).

## Inputs

The author's review, written against the NeurIPS review template we send them
(Summary; Strengths and Weaknesses; Criterion Ratings 1–4 table; Questions;
Limitations; Overall Score 1–6; Confidence 1–5). Convert to markdown first if
it arrives as a doc; keep the criterion table as a markdown table and mark
the selected overall/confidence options (`- **✓ N — Label.**` or `[x]`).

## Steps

```
python3 .claude/skills/collect-expert-ratings/collect.py --review <review.md>
```

Emits the four criterion scores, the overall score + label, and confidence.
Transcribe into the ratings `:::table-figure` (dots = score, e.g. ●●○○ 2/4)
and use the review's one-line justifications for the "Summary of expert
comments" column (summarizing across reviewers is editorial — quote or
compress, never reinterpret).

## Verify

`python3 .claude/skills/collect-expert-ratings/test.py` — re-extracts both
CRUX-2 author reviews from the published article appendix and checks every
score against the published ratings table.
