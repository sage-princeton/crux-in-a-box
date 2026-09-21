---
name: figure-referee-rounds
description: >-
  Build the referee-rounds figure for a CRUX run — every isolated blind-review
  round as a row of facet ratings (Soundness / Presentation / Contribution,
  1–4) with the Overall recommendation, in time order — from the run
  workspace's reviews/blind_round_*.md files. Use when writing up a run's
  review trajectory or comparing how referees scored several runs.
---

# Referee rounds figure

Produces the **referee-rounds figure**: one pane per run, one row per blind
review round, the referee's facet scores as cells on the 1–4 ramp and the
Overall recommendation in the verdict column, in the CRUX 2 reviews-figure
style. Two steps: a deterministic collector over the review files and a
builder that renders one or more collected runs as stacked panes.

Paths below: `$SKILLS` is this skills directory (`analysis/skills` in
crux-in-a-box, `.claude/skills` inside an exported run repo); `$RUN` is the
run repo root (`runs-export/<run>/repo` in crux-in-a-box, `.` inside the
export); `$OUT` is an output directory of your choice (in crux-in-a-box use
`analysis/clean-room/<run>/`, which is gitignored).

## Inputs you need

1. **The reviews directory** of the run workspace: `$RUN/reviews/blind_round_N.md`,
   one file per isolated review (`scripts/review_blind.sh` writes them; they
   are evidence and are never regenerated). The collector reads the RATINGS
   block in any of the shapes the brief produces (bold bullets, plain bullets,
   a table; Overall with or without its label) and the `Recommendation:` line.
2. **When each round happened**, for the `T+…h` row labels. In priority
   order the collector uses: a `--times` JSON you author; the file's first git
   commit when `reviews/` sits inside the run repo export; a `git log --stat`
   rendering passed as `--gitlog` (a `crux-collect` pull ships
   `workspace/<container>/git/git-log.txt`); the header time of the first
   `LOG.md` entry that mentions the round (`--log`); and, last, the file's
   mtime — which after a collection is the pull time, so it is flagged.
3. **The run's start** for the hour arithmetic: `--timeline` (first model
   call) or `--start <ISO>`.

## Steps

1. Collect:

   ```
   python3 $SKILLS/figure-referee-rounds/collect.py \
     --reviews-dir $RUN/reviews \
     --timeline $RUN/host/<run>.timeline.jsonl.gz \
     --log $RUN/LOG.md \
     --label "GPT-6 Astra · Codex CLI" \
     --out $OUT/reviews.json
   ```

   Read the `warnings` array: a round whose ratings did not parse renders as
   dots, and a round timed from its mtime needs a real source.

2. Build (one pane per `--data`, in the order given):

   ```
   python3 $SKILLS/figure-referee-rounds/build.py \
     --data $OUT/reviews.json \
     --out $OUT/referee-rounds.png
   ```

   The subtitle is generated from the data (which facets, which scales, the
   best Overall and how often it was reached); `--subtitle` replaces it and
   `--title` the heading.

## Judgment rules

- Rows are the referee's numbers, verbatim. Never adjust a score, drop a
  round, or renumber; if a file is not a blind round (a source audit, a
  self-review), leave it out with `--pattern`, not by editing.
- A missing round number (round 1 lost, rounds renumbered by the agent) is a
  fact about the run: keep the gap and say so in the write-up.
- The verdict column is the Overall recommendation the brief asks for, not a
  count of criticisms; the collected JSON also carries the FATAL/MAJOR/MODERATE
  tally, minor-issue count and question count per round for the prose.
- Time labels come from evidence (`time_source` per row); prefer git over the
  log, and the log over mtime.

## Verify

`python3 $SKILLS/figure-referee-rounds/test.py` — parses three fixture
reviews covering every RATINGS shape the brief yields and checks the scores,
labels, tallies and time sources; renders the fixture figure if Chrome is
available; and, when the codex-astra run data is present (under
`runs-export/` or as the repo itself) and the codex-influence collection is,
reproduces their published ratings and round times.
