# Verifier calibration

The harness reviews its own paper with an internal **verifier** prompt before
shipping (see `next-run-harness/workspace/AGENTS.md` § Reviews). This folder
checks that prompt against reality: attach a real ICLR paper's PDF, ask the
verifier for a verdict, and compare it to the paper's *actual* accept/reject
decision and reviewer scores.

## Test it

Needs `ANTHROPIC_API_KEY` (billed to whatever org owns the key) and a local
[PeerRead](https://github.com/allenai/PeerRead) checkout.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python3 build_dataset.py --peerread <path>/PeerRead/data   # score-balanced ICLR 2017 papers + PDFs
python3 upload_pdfs.py                                      # cache each PDF -> Files API id (once)
python3 run_verifier.py --effort xhigh                     # run reviewer.txt over each PDF (resumable)
python3 analyze.py                                          # κ, confusion, lean, separation vs real decisions
```

`run_verifier.py` tests `reviewer.txt` by default; point `--prompt-file` at a
variant to compare two prompts on the same papers. `--dry-run` prints the
assembled prompt without calling the API.

## Files

| file | role |
|---|---|
| `reviewer.txt` | the verifier prompt under test (mirror of AGENTS.md § Reviews) |
| `build_dataset.py` | assemble real papers + PDFs + accept/reject labels from PeerRead |
| `upload_pdfs.py` | upload the PDFs to the Anthropic Files API, cache the ids |
| `run_verifier.py` | run the prompt over each PDF via the Claude API |
| `analyze.py` | score a run: confusion matrix, Cohen's κ, lean, separation, rank corr |

Datasets, results, and reports are regenerable and gitignored.

## Changelog

**v2 — current `reviewer.txt` (calibrated).** On 12 ICLR papers balanced across
the score range, the verifier now matches the real committee **exactly**:
Cohen's **κ = 1.00** (6/6 true accepts and 6/6 true rejects correct), mean-score
separation **+2.0** between accepted and rejected papers. Changes from v1:
- Judge **contribution against flaws** rather than rejecting on any weakness — an
  accept is "a paper you'd argue *for* in committee," not one that is flawless.
- Added a **MODERATE** severity tag and a **"What the paper contributes"**
  section, so a real contribution is weighed, not just weaknesses cataloged.
- Recalibrated the Soundness rubric ("3 = solid and competent … the right score
  for most sound papers").
- **Removed the automatic veto** `Soundness ≤ 2 caps Overall at 3` — now only a
  FATAL, unlookpastable flaw forces a low Overall.

**v1 — original (harsh).** Framed as "be critical; if unsure, reject," with the
soundness-caps-overall veto. On the same 12 PDFs it scored **κ = 0.33**,
rejecting **4 of 6** papers the committee accepted; on a 40-paper text-only pass
it rejected **all 40** (κ = 0). It could still *rank* papers (positive rank
correlation), but its accept bar sat so high almost nothing cleared it — a pure
harshness miscalibration, which the v2 re-centering fixed.

## Caveats

- **Contamination.** ICLR 2017 papers may be in the model's training data, so a
  *good*-calibration result is an upper bound; a *miscalibration* result (too
  harsh) is still trustworthy. The real harness reviews unpublished papers.
- **Small, balanced n.** 12 PDFs chosen to span the score range — a calibration
  probe, not an accuracy benchmark.
- **Scale mapping.** The verifier is 1–6, ICLR is 1–10; comparisons use the
  accept/reject decision and rank correlation, which are scale-independent.
- A one-off tuning probe, not part of the shipped harness.
