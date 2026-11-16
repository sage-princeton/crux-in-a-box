# Verifier-Free Test Time Scaling

The full research profile is `scaffold.toml`: seven days, $15,000 for PI/assistant/
coding/review inference, and an independently supplied $3,000 experiment
allocation. The separate `smoke/` profile rehearses the same credit distinction
for 20 minutes with $8 inference and $4 experimental allowance. These local test
allowances are cooperative; the supplied provider caps are the billing backstops.

Use the local isolated OrbStack Ubuntu environment in `ciab-devenv`. No AWS
deployment is part of this preparation. Its services, Langfuse and cost collector
are local; inference and subject-model requests reach OpenRouter. Do not enable
live cloud, Slack, email or production telemetry overrides for this rehearsal.

## Private inputs

Use a private JSON with `OPENROUTER_API_KEY` and
`OPENROUTER_EXPERIMENT_API_KEY`, or the operator's two-line `vftts.temp` format
(inference first, experiment second). Never add that file to Git or caller input
snapshots. Verify each key's cap and billing route via OpenRouter before use.
Two keys share account-funding constraints; reserve enough funds for both pools.
The preparation performed on November 16 found fresh $150/$50 non-resetting keys;
those are test credentials, not confirmation of full-run $15,000/$3,000 funding.

Run from the selected crux-in-a-box worktree, using an output directory outside
all Git checkouts. The preparation helper copies only the smoke drop-in and
writes credentials separately, mode 600:

```sh
python3 scaffolds/sdk/run-harness/vftts/prepare_local.py \
  --credentials /private/path/vftts.temp \
  --output /private/tmp/vftts-rehearsal \
  --experiment-provider-backstop-usd 50
```

The supplied test keys were observed routing OpenAI through BYOK, with BYOK
included in both key caps. For those keys add `--experiment-billing byok` to the
preparation command. The default `credits` mode rejects unexpected BYOK. BYOK
mode requires an inclusive provider cap and counts upstream inference separately
from OpenRouter charges; zero router credit usage is not a free request. Returned
billing must match the explicit mode. Neither mode automatically falls back.

For a full run, fill `secrets.json.example` in a private directory. Set a dedicated
experiment key cap at or below $3,000, non-resetting. The default experiment client
requires that relationship; local tests may explicitly declare a larger provider
backstop with `OPENROUTER_EXPERIMENT_KEY_CAP_USD`, keeping the smaller cooperative
run allowance. Management keys and AWS credentials never belong in run secrets.
Use one stable absolute `OPENROUTER_EXPERIMENT_STATE_DIR` shared by all workers.

## Local Linux rehearsal

Use one named ciab-devenv environment and the isolated feature worktree. Start
with `status` and `doctor`, then configure/provision; do not combine unrelated PR
stacks. The local environment must support the current `devops/`, `scaffolds/`
and `monitoring/` layout and explicit SDK OpenRouter selection. New source files
must be in the Git index because source staging copies tracked files, including
edits. Caller inputs are copied from the private prepared directory above.

```sh
cd /path/to/ciab-devenv
export CIAB_STATE_ROOT=/private/tmp/ciab-vftts-state
export CRUX_MODEL=openai/gpt-6.1-sol CRUX_REASONING_EFFORT=low
./devctl --name vftts status
./devctl --name vftts doctor
./devctl --name vftts bootstrap --source /path/to/crux-worktree \
  --scaffold sdk --provider openrouter --port 23800 \
  --drop-in /private/tmp/vftts-rehearsal/drop-in \
  --secrets-json /private/tmp/vftts-rehearsal/run-secrets.json
./devctl --name vftts check
./devctl --name vftts run --timeout 1200
./devctl --name vftts report RUN_ID --output /private/tmp/vftts-evidence
./devctl --name vftts down
./devctl --name vftts machine status
```

Provisioning performs billed inference probes before the 20-minute run; include
them in spending. The bounded run starts the production systemd unit and stops it
at timeout. Keep the named environment shut down between tests. Preserve reports
and state. Reprovision after source, model, dependencies or overrides change.

Acceptance requires evidence beyond a model's success claim: PI, SDK assistant
and Codex worker must each invoke the experiment client, generating separately
attributed receipts, while their reasoning remains on the inference key. Exercise
a native child when supported and report unsupported coverage. Compare both
provider keys' before/after usage with experiment receipts, persisted inference
usage and local Langfuse; allow for ingestion/settlement delay and do not add
overlapping measurements. Inspect refreshed SDK resource context and experiment
submission status. The launch brief does not prove every native Codex inner turn
has current status (AE-276).

Offline tests cover invalid keys, limits, restart binding, concurrency/deduplication,
stale status and exhaustion without stopping inference. Use those fixtures for
faults rather than deliberately consuming a $50 provider cap. Record the live
rehearsal's exact branch/commit/edits, model/provider/effort, service locality,
commands, duration, report/trace/artifact paths, failures and remaining gaps.
Export only sanitized evidence, never raw sessions or credential files.

The November 16 local rerun passed its receipt and independent-review gates:
four experimental receipts reconciled to $0.005826 of upstream BYOK usage,
separate from $0.2275181 on the inference key. Native-child inference accounting
remains unresolved: the child session reported 32,815 tokens while the saved
worker ledger matched the parent session's 43,955 tokens. Do not treat the
parent ledger as complete child accounting. Prefer scaffold SDK assistants for
delegation until native-thread usage is reconciled. This two-minute synthetic
smoke did not validate benchmark results, compaction or a full research budget.

## Research inputs and outputs

`workspace/evaluation.json` pins official dataset revisions and an initial
three-family model pool. From the run workspace, install
`requirements.txt`, then run `python prepare_data.py gpqa_diamond` and
`python prepare_data.py hle_verified_text_mc`. GPQA requires authorized HF access;
do not substitute a mirror. The loader writes private questions and labels to
separate files with a revision/hash/ID manifest and deterministic development/test
split. It excludes uncertain, image and non-MC HLE items. Report this subset's
identity and count; it is not the complete benchmark. The 96.5%/63.6% motivation
is unverified for this exact pool/subset and is not a required result.

Baselines include majority, explicitly defined SP starting variants and
confidence-selected best-of-N. Reconcile their multiclass formulation with the
original Prelec paper before reporting raw-SP results. Held-out labels are for
offline scoring after predictions are sealed. The oracle is an upper bound.
ARC-AGI 2 and BuilderBench are deferred. Review prompts reject verifiers, debate,
label leakage, unreported selection, and claims without comparable evidence.

The research loop preserves 10% of inference and two hours for finalization.
Mechanical artifact checks and an isolated paper reviewer check the paper and
reproducibility package. Experiment exhaustion never triggers the whole-run stop.
Collect `paper/paper.pdf`, code, `RESULTS.md`, `reproduce.sh`,
`COMPLETION_REPORT.md`, private data manifests, receipts and run state.

Primary sources: [Prelec et al.](https://doi.org/10.1038/nature21054),
[official GPQA](https://huggingface.co/datasets/Idavidrein/gpqa),
[HLE-Verified](https://huggingface.co/datasets/skylenage-ai/HLE-Verified),
[OpenRouter key metadata](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key),
[usage receipts](https://openrouter.ai/docs/cookbook/administration/usage-accounting).
