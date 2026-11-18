# Verifier-Free Test Time Scaling

When many independent LLM samples are available but no verifier can check them,
how should a heterogeneous pool's samples be aggregated into a single answer
that beats majority vote on hard multiple-choice questions? Study correlated
errors across model families: a popular answer can be a shared wrong answer.
The supplied motivating figures, 96.5% pool oracle versus 63.6% majority on
HLE-Verified, have an unspecified original pool/subset. They are reference claims,
not facts established by this run or acceptance thresholds. The original task
was marked unfinished and its target paper unpublished on October 20, 2026.
Do not search for that target paper or attempt to reconstruct its hidden method.

Start with Prelec, Seung and McCoy (2017), *A solution to the single-question
crowd wisdom problem*, https://doi.org/10.1038/nature21054 . Each rollout returns
an answer and predicts the distribution of answers from the declared crowd.
Investigate whether unexpectedly frequent minority answers contain useful
information. Check the multiple-choice formulation in the paper and supplement;
do not transfer its assumptions or guarantees directly to correlated LLMs.

The initial scope is GPQA Diamond and HLE-Verified. evaluation.json pins their
source revisions, subset policy, seed, initial three-family pool and K grid.
Use the official GPQA source after authorized access. For HLE-Verified, use only
Gold/Revision, text-only, explicitly multipleChoice items; exclude Uncertain and
do not convert free response into multiple choice. Report that subset's count and
IDs rather than claim accuracy on all HLE-Verified. ARC-AGI 2 and BuilderBench
have different task/output contracts and are deferred from this cheaper run.

In the first hour, prepare data and collect a small real pilot. Plot majority and
the pool oracle versus K, estimate pairwise error correlations (including within
versus across families), and examine questions where majority fails but a saved
sample is correct. If the gap is small or absent, investigate and report this
instead of manufacturing the motivating percentages.

Preserve rollout independence: fresh requests, no preceding samples or crowd
results in a subject's context, no debate, no answer-ground-truth requests, no
correctness verifier trained or called at inference. Do not send labels,
rationales, verification metadata or dataset names with obvious answer lookup
opportunities to subject models. Describe the actual pool and its sampling
weights when eliciting a crowd forecast. Predictions must cover every answer
option, be nonnegative and sum to one; define invalid/abstention handling before
evaluation. Record generation/model/provider IDs, settings, token/cost receipts,
sample indices and option permutations. Reused sample prefixes must be fixed
before scoring, with each model call's cost counted once.

Freeze baselines before method exploration: majority vote; raw SP with an exact,
cited multiple-choice formula; and best-of-N selected by a declared label-free
criterion. The supplied baselines.py offers **SP-gap** (observed vote share minus
mean forecast) and **SP-ratio** as transparent starting operationalizations, not a
claim that they exhaust the original paper's multiclass method. Preserve both
when comparing. Its best-of-N uses reported confidence, with its calibration
limitations explicit. Any label-selected best-of-N is the pool oracle, never a
deployable baseline. Use identical sample pools across aggregators, deterministic
tie rules and matching K/cost budgets. Include single-model and family-balanced
comparisons so pool composition cannot masquerade as an aggregation gain.

Primary metrics: top-1 accuracy and paired percentage-point gain over majority.
Report item-level bootstrap intervals, K and actual-dollar curves, invalid
response rates, and the pool oracle as an offline upper bound. Separate method
development from final evaluation using the deterministic ID split. Freeze
method/configuration and seal predictions before scoring held-out labels.
Labels may support offline evaluation and error diagnosis, never per-question
inference. Do not train a correctness model. Preserve negative results and all
tested variants to expose selection effects. Build one well-motivated extension
to the SP starting point; novelty claims need literature support and ablations.

Deliver a concise research paper, code, reproducibility commands, RESULTS.md
with evaluation identities and uncertainty, and COMPLETION_REPORT.md with
separate inference/experiment spend and all limitations. Ground claims in saved
evidence. Use the finalization reserve for synthesis, not unbounded new sweeps.
