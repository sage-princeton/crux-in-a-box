# Verifier calibration

The harness reviews its own paper with an internal **verifier** prompt before
shipping (`next-run-harness/workspace/AGENTS.md` § Reviews, on the
`scaffold-spike/openclaw-v3` branch). This folder checks that prompt against
reality: attach a real ICLR paper's PDF, ask the verifier for a verdict, and
compare it to the paper's *actual* reviewer scores and accept/reject decision.

`reviewer.txt` is a verbatim copy of that brief, differing only in how the paper
is delivered — the harness points a subagent at a path and tells it to run
`pdftotext`; here the PDF is attached to the API message. Re-diff the two after
any change to either.

## What this measures

**Tail behaviour first.** Does a genuinely strong paper get a high score and a
genuinely weak one a low score? That is the property the harness depends on — a
verifier that compresses everything into the middle cannot tell the run whether
its paper is good.

Accept/reject agreement is reported but is *secondary*, because on this corpus
it is nearly free: ICLR 2017's decision is close to a threshold on the mean
reviewer score, and a rule of "accept iff mean ≥ 6.0" — which never opens the
PDF — scores **89.5%** across all 427 papers. A high κ on a score-balanced
sample mostly measures how few borderline papers the sample contains.

## The venue-banner leak (read this before trusting any result)

PeerRead stores whatever revision was on OpenReview when it was scraped. For an
accepted paper that is the **camera-ready**, whose running header reads

    Published as a conference paper at ICLR 2017

while a rejected paper keeps

    Under review as a conference paper at ICLR 2017

The banner repeats on nearly every page. On the 40-paper set it predicts the
venue decision on **37 of 40 papers**, and it is worst exactly where this probe
is supposed to be measuring something: **8 of the 10 top-tail papers announce
their own acceptance**, while 9 of the 10 bottom-tail papers say "under review."
A model can pass the tail test by reading the header.

This is sharper than the training-data contamination noted under Caveats, since
it does not require the model to remember the paper — the label is printed on
the document.

`redact_banners.py` normalizes every banner to the "Under review" form so all
papers look like submissions, and verifies the result with `pdftotext`. It is a
content-stream text substitution, so layout, figures, and the text layer are
untouched (measured word-count drift ≤ 0.25%, and positive, because "Published"
becomes two words). **Run it before uploading**; `run_verifier.py --pdfs`
defaults to the redacted build.

Keeping both builds lets you run the ablation — same papers, same prompt, banner
visible vs. normalized — with `compare_arms.py`. Separation that survives the
redaction is what the verifier earned from the science.

## Train and test

`build_dataset.py` writes both splits to `dataset.jsonl` with a `split` field.

- **train (20 papers)** — iterate on the prompt here.
- **test (20 papers)** — held out. Score a prompt on it only after you've stopped
  changing the prompt.

The two are matched stratum-by-stratum on extremity (candidates are ranked, then
dealt alternately), so a result on one should replicate on the other. A large
train/test gap means the prompt was fitted to train.

This matters because it was previously absent: the v2 prompt's reported κ = 1.00
was measured on the same 12 papers whose v1 failures motivated the v2 edits — an
in-sample number.

## Sampling

Strata are weighted toward the ends of the reviewer-score range rather than
spread uniformly across it, because the tails are the question. The middle bands
are present but thin — they exist to make the score curve's monotonicity
measurable, not to be the test.

| stratum | mean human score | per split | why |
|---|---|---|---|
| `bottom_tail` | < 3.5 | 5 | terrible papers — must score low |
| `low` | 3.5 – 5.0 | 3 | weak rejects |
| `mid` | 5.0 – 6.5 | 4 | the contested band (monotonicity anchor) |
| `high` | 6.5 – 7.5 | 3 | solid accepts |
| `top_tail` | ≥ 7.5 | 5 | great papers — must score high |

Two things make a tail paper a fair test, and selection enforces both: it is
extreme, **and** its reviewers agreed (low max–min spread, decent confidence). A
verifier that misses such a paper is wrong about one real reviewers found
unambiguous. The pool supports this comfortably — all 26 papers below 3.5 have a
spread ≤ 2, as do 31 of the 36 at or above 7.5.

`--scale` multiplies every stratum's count if you want a bigger run.

## Test it

Needs `ANTHROPIC_API_KEY` and a PeerRead checkout. A full clone is ~1GB and this
needs 40 PDFs, so a blobless partial clone is enough:

```bash
git clone --filter=blob:none --no-checkout https://github.com/allenai/PeerRead.git
cd PeerRead && git sparse-checkout init --cone \
  && git sparse-checkout set data/iclr_2017/train/reviews \
       data/iclr_2017/dev/reviews data/iclr_2017/test/reviews \
  && git checkout && cd -
```

```bash
python3 -m venv .venv && ./.venv/bin/pip install anthropic openai pypdf
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-proj-...        # only for --provider openai

./.venv/bin/python build_dataset.py --peerread <path>/PeerRead/data  # select 20 train + 20 test
./.venv/bin/python fetch_pdfs.py                                     # materialize just those PDFs
./.venv/bin/python redact_banners.py                                 # strip the venue banner
./.venv/bin/python upload_pdfs.py --variant redacted                 # cache each PDF -> Files API id
./.venv/bin/python run_verifier.py --split train --effort xhigh      # resumable
./.venv/bin/python analyze.py --results results_train_claude-fable-5_redacted.jsonl
```

`--dry-run` prints the assembled prompt without calling the API.
`--prompt-file` points at a variant to compare two prompts on the same papers.

### Other providers

The runner is provider-agnostic — same prompt, same papers, same parser, same
output schema, so results are directly comparable. Only the API call differs.

```bash
./.venv/bin/python upload_pdfs.py  --provider openai --variant redacted
./.venv/bin/python run_verifier.py --provider openai --split train --effort xhigh
```

| provider | default model | thinking control | PDF delivery |
|---|---|---|---|
| `anthropic` | `claude-fable-5` | `output_config.effort` + adaptive thinking | Files API -> `document` block |
| `openai` | `gpt-5.6-sol` | `reasoning.effort` | Files API (`purpose="user_data"`) -> `input_file` |

Both accept `xhigh`. Both render the page images, so the figures the prompt asks
about are actually visible — verified by asking each model to describe a
figure's colours, which appear nowhere in the text layer.

### Reviewing a paper that isn't in the dataset

The runner is dataset-driven, so an ad-hoc paper needs a one-row dataset. There
is no ground truth for it, so `analyze.py` and `compare_arms.py` have nothing to
score against — this yields the review and its ratings, not a calibration number.

```bash
cat > dataset_mypaper.jsonl <<'EOF'
{"id": "mypaper", "title": "...", "pdf_path": "/abs/path/main.pdf", "stratum": "unlabeled"}
EOF

./.venv/bin/python upload_pdfs.py --dataset dataset_mypaper.jsonl \
  --variant original --cache pdf_file_ids_mypaper.json

./.venv/bin/python run_verifier.py --dataset dataset_mypaper.jsonl --split all \
  --pdfs original --file-ids pdf_file_ids_mypaper.json \
  --model claude-fable-5 --effort xhigh --out results_mypaper.jsonl
```

Use `--pdfs original`: `redact_banners.py` normalizes the ICLR camera-ready
header, and an unpublished manuscript has none, so the redacted path is a no-op.
The console prints `true=REJ` for an unlabeled row — that is `row.get("accepted")`
defaulting to falsy, not a claim about the paper.

Note that the two providers ingest the same PDF differently (Anthropic renders
pages alongside extracted text, OpenAI extracts text), so input token counts —
and any figure-dependent judgment — are not strictly matched across providers.

### Comparing two runs

`compare_arms.py` diffs any two result files over the same papers and labels the
arms by whatever actually differs (PDF build, model, effort, prompt):

```bash
# banner ablation
./.venv/bin/python compare_arms.py \
  --a results_train_claude-fable-5_banner.jsonl \
  --b results_train_claude-fable-5_redacted.jsonl

# model comparison
./.venv/bin/python compare_arms.py \
  --a results_train_claude-fable-5_redacted.jsonl \
  --b results_train_gpt-5.6-sol_redacted.jsonl
```

**Cost:** ~$0.60/paper on Fable 5 at `xhigh` (~32k in, ~6k out), so ~$12 per
20-paper split and ~$24 for both.

## Files

| file | role |
|---|---|
| `reviewer.txt` | the verifier prompt under test (copy of AGENTS.md § Reviews) |
| `build_dataset.py` | select train/test papers + accept/reject labels from PeerRead |
| `fetch_pdfs.py` | materialize the selected PDFs from a partial PeerRead clone |
| `redact_banners.py` | normalize the ICLR venue banner so the PDF stops leaking the verdict |
| `upload_pdfs.py` | upload the PDFs to the Anthropic Files API, cache the ids |
| `run_verifier.py` | run the prompt over each PDF via the Claude API |
| `analyze.py` | score a run: tails, monotonicity, scale use, then κ/confusion |
| `compare_arms.py` | diff two runs over the same papers (banner ablation, or model vs model) |

Datasets, results, reports, and `.venv/` are regenerable and gitignored.

## Notes on the runner

- **Provider / model / effort.** `--provider anthropic` (default, `claude-fable-5`)
  or `--provider openai` (`gpt-5.6-sol`), both at `xhigh`. Result files are
  named `results_<split>_<model>_<arm>.jsonl` — the resume logic skips ids already
  in the file, so sharing one file across models would silently mix two models'
  verdicts into one "calibration".
- **Streaming.** Fable 5 at `xhigh` thinks for minutes on a full paper, which
  overruns a non-streaming HTTP timeout.
- **Refusals are recorded, never retried on another model.** A server-side
  fallback would put a different model's review in the results file. `analyze.py`
  reports unparsable and refused responses rather than dropping them silently.
- **PDF variant.** `--pdfs redacted` (default) reviews the banner-normalized
  build; `--pdfs original` reviews the as-scraped PDFs. Each variant has its own
  file-id cache and its own `results_<split>_<model>_<arm>.jsonl`, so the two
  arms cannot contaminate each other on resume.
- **Verdict parsing.** `score_1to6` follows the `Recommendation:` line;
  `overall_raw` is the Overall rating, parsed independently so the two can be
  compared. `verdict_mismatch` is set when they disagree — the prompt forbids
  narrating one verdict while scoring another, and this is the mechanical check
  for it.
- **Files API ids are org-scoped.** Switching API keys across organizations
  invalidates `pdf_file_ids.json`; delete it and re-upload.

## Caveats

- **Contamination.** ICLR 2017 papers are in the model's training data, so a
  *good* result is an upper bound. A *miscalibration* result is still
  trustworthy, which is what this probe is for. The real harness reviews
  unpublished papers.
- **Small n by design.** 20 per split is a calibration probe, not an accuracy
  benchmark. At n = 20 a perfect score still has a 95% lower bound near 83%;
  read the tail columns, not the third decimal.
- **Scale mapping.** The verifier is 1–6, ICLR is 1–10; comparisons use the
  decision, the tail thresholds, and rank correlation, which are
  scale-independent.
- **The ground truth is duplicated in PeerRead.** Every paper's `reviews` list is
  stored concatenated with itself; `build_dataset.py` de-duplicates before
  counting. Reading it naively gives exactly 2× the true review count (6/8/10
  instead of 3/4/5) — means are unaffected, but anything count- or
  variance-weighted is not.
- A tuning probe, not part of the shipped harness.

## Changelog

**v3.2 — the Overall rating was never parsed.** `parse_verdict` required the
literal `Overall (1-6)`, which no model writes: Fable 5 emits `**Overall: 5**`,
gpt-5.6-sol emits `Overall (1–6): 4` with an en dash, or `**Overall:** 4/6`.
`overall_raw` was therefore `None` on all 83 stored records, and `score_1to6`
always fell through to the Recommendation line — silently disabling the
rec-vs-Overall cross-check. Re-parsing the stored reviews with the fix recovers
83/83 with zero mismatches, so no reported number was wrong; the check simply
never ran. Added `verdict_mismatch` to the record and a `<-- MISMATCH` console
flag, and documented reviewing an ad-hoc paper.

**v3.1 — the venue-banner leak.** Found that the as-scraped PDFs announce the
venue decision in a running header on nearly every page (37/40 predictable from
the banner alone; 8 of 10 top-tail papers). Added `redact_banners.py` to
normalize it, a `--pdfs` variant flag through the uploader and runner, and
`compare_arms.py` to measure what the banner was worth. Every result produced
before this is contaminated.

**v3 — tail-weighted sampling, train/test split, Fable 5.** Rebuilt the dataset
and fixed the sampler. The v2 design drew 2 papers from each of 6 uniform
score bins, which put **10 of its 12 papers in bins where the accept/reject
label is ≥ 98% predictable from the score alone**; only the [5.5, 6.5) bin
carried real difficulty, and it got 2 slots. Changes:
- Strata re-weighted toward the tails; selection now prefers extreme papers with
  *agreeing* reviewers, so a tail miss is unambiguous.
- Train/test split, matched on extremity.
- `analyze.py` leads with tail separation, per-stratum monotonicity, and scale
  use; κ and the confusion matrix demoted to secondary.
- Fixed: PeerRead's duplicated review lists (n_reviews was 2× truth); unsorted
  `glob.glob`, which made the sample machine-dependent despite the fixed seed.
- Runner moved to the Anthropic SDK with streaming, refusal recording, and
  model-scoped result files.

**v2 — the calibrated prompt (in-sample).** Reported Cohen's κ = 1.00 and +2.0
mean-score separation on 12 ICLR papers. Treat that number as in-sample: the
same 12 papers motivated the edits. Changes from v1 — judge contribution against
flaws rather than rejecting on any weakness; added a MODERATE severity tag and a
"What the paper contributes" section; recalibrated the Soundness rubric; removed
the automatic `Soundness ≤ 2 caps Overall at 3` veto.

**v1 — original (harsh).** "Be critical; if unsure, reject," with the
soundness-caps-overall veto. On the same 12 PDFs it scored κ = 0.33, rejecting 4
of 6 papers the committee accepted; on a 40-paper text-only pass it rejected all
40 (κ = 0). It could still *rank* papers, but its accept bar sat so high almost
nothing cleared it.
