---
title: "feat: Run a bounded contrastive RL pilot"
type: feat
status: active
date: 2026-10-29
---

# Contrastive RL pilot

## Goal and scope

Run one six-hour CRUX to learn whether the research question, supplied CRTR code,
GPU access, agent configuration and trace capture form a useful research
environment. Give the agent the supplied question/context and repository with a
short description. Let it choose and justify its method. Analyze the trace and
artifacts afterward to decide what to change before a longer run.

Launch preparation and execution remain outstanding. The environment has not been
tested on a live run box. The confirmed research budgets are **$100 for agent inference** and
**$30 for RunPod**, over at most **six hours**. These are separate allowances;
AWS infrastructure is outside them.

No broad scaffold redesign, full benchmark reproduction, multi-domain sweep or
claim to have identified a universally correct geometry belongs in this pilot.

## Preparation status

The run bundle is implemented at `contrastive-reinforcement-learning/pilot/`.
Its `scaffold.toml` uses OpenRouter Responses, the existing SDK research loop and
review gates, unattended operation, three coding workers, 21600 seconds, $100 inference
and $30 RunPod. `prepare.py` validates/extracts the pinned archive into a fresh
bundle. `preflight.py` reports missing inputs
and can perform read-only service checks. `export_trace.py` extracts and scrubs
SDK sessions plus run-filtered Langfuse observations on the box before transfer.
`launch.py` calls the fleet provisioner, stages a verified source/scanner bundle,
deducts prelaunch inference spending, checks the service and starts the pilot.
Concrete deployment and launch commands are in its `OPERATOR_GUIDE.md`.

The supplied archive has been staged in a temporary validation bundle and passed
the actual scaffold CLI check. Tests cover configuration, archive failures,
resource reserves/deadlines, cleanup and scrubbed export. No research was launched,
no GPU was allocated and no inference was spent. AWS access and
live tracing/cost-service configuration remain external launch inputs.

The OpenRouter key passed a read-only check: unused, non-resetting **$250** key
limit, which remains unchanged. The agent is instructed to spend at most $100
across the PI, delegates and reviews, and the TOML retains that budget. The bundle's model default
is `anthropic/claude-sonnet-5.5`, high effort, present in the provider catalog;
actual traced SDK/Codex inference is still a launch check. The private provisioning
config and supplied keys are saved under ignored `src/ec2-workspaces/` paths.

## Requirements trace

- **R1 — Research inputs:** preserve the supplied question and context; provide
  the supplied CRTR archive, with provenance and a one-line explanation.
- **R2 — Comparable trial:** reuse the prior pilot setup where available, record
  its model and scaffold revision, and run for at most six hours.
- **R3 — Resources:** establish explicit model, GPU and infrastructure allowances,
  verified credentials, spend visibility and cleanup before research starts.
- **R4 — Evidence:** preserve a useful trace and reproducible research artifacts;
  assess environmental feasibility separately from scientific progress.

## Research input to carry into the run

**Research question, supplied by the user:**

> What is the correct geometry for representing complex reasoning tasks?

**Context, supplied by the user:**

> CRTR is a method for solving complex reasoning tasks, based on representations
> learnt through contrastive reinforcement learning. The representations that
> were learnt so far are symmetric --- they are as though going forward in time
> has the same probability as going backward in time. Based on this codebase,
> figure out a good parametrization that would allow us to model the fact that
> these probabilities aren't necessarily equal. When designing the method like
> that, we also need to have an intuitive/mathematical/experimental justification
> for why a certain form should be chosen over a different one, make sure to keep
> that in mind.

**Additional context line:**

> The accompanying CRTR repository provides the reference implementation,
> training/evaluation configurations and example checkpoints; use it as the
> starting codebase and retain its original symmetric scorer as a baseline.

Stage the supplied archive as a separate research checkout in the agent workspace.
Use the supplied question/context and the additional context line above as the
research input. Let the agent choose its geometry.

## Findings from the supplied archive

Input: `contrastive-reinforcement-learning/CRTR-main.zip`.
Its ZIP comment identifies revision `d100ecf1dc6f76ce3299862e494f6a750ad94346`;
its SHA-256 is
`9a1734f6e11608f2b4e235c752028657c01cd097beacc6c57d09bcda2cb38cc0`.
The archive is ignored by Git, so provisioning from a committed checkout will not
automatically deliver it. Transfer it separately and verify its hash on the box,
using the supplied archive as the pinned source.

Relevant archive paths below are relative to `CRTR-main/`:

- `README.md` recommends Python 3.10; `requirements.txt` pins Torch 2.3.1,
  NumPy 1.26.4, JAX 0.6.2 and Jumanji 1.1.1, with a Git dependency for Rubik.
  Installation compatibility and GPU operation are unverified.
- `cluster/` assumes Slurm, Singularity/Apptainer and institutional filesystem
  paths. Use `runner.py` and explicit local paths on RunPod rather than these
  launch scripts unchanged.
- The Rubik example includes a trajectory file. The Sokoban example directory
  contains a lengths file but no accompanying trajectory file. Eval boards are
  present under `training_datasets/sokoban_eval_boards/`; the README gives a
  different path. Example checkpoint directories exist only for Rubik and Sokoban.
- Full datasets range from roughly 955 MB for Colorful Maze to 14–14.5 GB for
  several other domains, according to the README. Download only what the selected
  pilot experiment needs. Host RAM and temporary disk also need measurement.
- Training configs commonly request **500,000,000 steps**. Override training,
  evaluation problem counts and search budgets using measured pilot throughput.
- `losses.py` already distinguishes forward/backward/symmetric normalization.
  `search/value_function.py` applies one encoder to states and goals and uses
  symmetric distance/dot-product scores. A loss-mode switch alone is not evidence
  of an asymmetric scoring geometry.
- The checkpoint loader accepts partial same-shaped weights and leaves missing
  parameters initialized. Record coverage; a successful load alone does not
  establish a valid baseline.

The user's message supplies the complete research question/context. Max's exact
pilot configuration has not been identified locally. No `docs/solutions/`
learnings were found.

## Budget and operating decisions

Confirmed allocations:

| Category | Allowance | Includes |
|---|---:|---|
| Agent inference via OpenRouter | $100 | Preflight plus all agent, delegate and review calls |
| RunPod | $30 | GPU runtime and associated storage |
| **Combined research allowance** | **$130** | AWS infrastructure and human time excluded |

Leave the supplied OpenRouter key's provider limit unchanged; verify the
chosen model works with the actual scaffold, including any coding delegates.
The agent's total authorized inference spend remains $100, independent of the
provider account limit. Keep a spend ledger and check budget_status before work.
Avoid introducing BYOK routing for this trial unless it was part of the reference
pilot. [OpenRouter key controls](https://openrouter.ai/docs/api/api-reference/api-keys/create-a-new-api-key).

Quote an initial single-GPU configuration and hourly ceiling before allocating a
smoke-test pod; confirm or revise its GPU/VRAM using the measured peak memory and
throughput. Do not assume an A100 is needed.
Allow no more than one active pod for this trial. Bound runtime and storage within
the $30 allocation, tag the pod with the run slug, and copy results continuously.
Stopping a pod can retain billable storage; termination deletes non-network-volume
data, so retrieve artifacts first. Avoid separate persistent volumes for this
trial. [RunPod lifecycle and storage](https://docs.runpod.io/pods/manage-pods).

These allocations are spending controls and a planning target, not an invoice
guarantee: the SDK example explicitly documents delayed billing measurements.
Count preflight spending against the same allocation and configure research limits
from the amount remaining. Track AWS infrastructure separately and set an EC2
teardown time after export.
Unknown/stale spend is a blocker to further discretionary compute, not zero.

The six-hour research clock starts with the agent, includes research package/data
setup, and does not reset on resume. Reserve the last hour and 10% of model/GPU
allowances ($10 inference and $3 RunPod, before any preparation spending) for
results retrieval and finalization. Preserve the existing pilot's
within-turn deadline enforcement and owned-pod cleanup, rather than assuming a
prompt or between-iteration check enforces the deadline.

## Work and sequence

- [x] **1. Freeze the input and reference pilot (R1, R2).**

  Locate the available prior pilot configuration, including scaffold commit,
  model, reasoning effort, topology, budget definitions and output contract.
  Record source revisions/hashes and stage the supplied brief plus the additional
  context line. Proposed operator artifacts:
  `contrastive-reinforcement-learning/pilot/INPUTS.md` and
  `contrastive-reinforcement-learning/pilot/OPERATOR_GUIDE.md`; agent-facing inputs
  go into the selected pilot bundle's existing context files.

  The local comparison pattern is
  `src/sdk-scaffold/examples/tabpfn/OPERATOR_GUIDE.md` and its `scaffold.toml`:
  a six-hour SDK research run with tracing, reserves and RunPod cleanup. Its actual
  allowances are **$200 models + $100 RunPod, excluding EC2**; it is not evidence
  of Max's $100 pilot. If Max's setup is unavailable, record an explicitly selected
  replacement baseline before launch, rather than claiming a matched repeat.
  The selected replacement is recorded in
  `contrastive-reinforcement-learning/pilot/INPUTS.md`: existing SDK example at
  main revision `f49acf8`, with task/routing/budget changes listed explicitly.

  Verification: the staged archive hash matches; the exact input
  files and chosen baseline are recorded. Test expectation: document and staging
  checks; no new unit tests for copied research prose.

- [ ] **2. Prepare a fresh run and prove readiness (R2, R3, R4; depends on 1).**

  Reuse the selected pilot's provisioning, orchestration and reporting. Change
  research inputs, source staging and budget values consistently across config,
  prompts and operator notes. Record every difference from the reference pilot.
  Keep shared infrastructure changes outside this trial unless a demonstrated
  blocker requires one. Current SDK patterns are documented in
  `src/ec2-workspaces/README.md`; provisioning stages committed `HEAD` and leaves
  `crux-sdk-run` stopped until preparation finishes. Existing unrelated local SDK
  edits must not silently become part of the run.

  Required resources: an AWS profile/session with provisioning permissions,
  region and SSH access; the supplied OpenRouter key; a funded RunPod allocation/key;
  existing tracing/cost-tracker access.
  SDK mode can run without an AgentRQ controller. Use existing fleet services
  where available; any new service provisioning is additional preparation.
  Run unattended with progress and blockers recorded in PLAN.md, LOG.md and the
  completion report; monitor the systemd journal and Langfuse.
  Keep operator AWS credentials on the operator side; the agent gets only its
  model, GPU and tracing credentials, through ignored private
  bundles. Never put keys in the research input or plan.

  Readiness evidence: successful scaffold/delegate provider probes, a trace with
  priced usage covering both orchestrator and any delegates, observable GPU
  billing, pod create/connect/delete, and a small
  import/checkpoint/forward-pass/training/evaluation smoke test on the intended
  runtime. Measure step time, peak memory and download time. Capture repairs as
  setup changes, preserving the supplied scientific implementation as the baseline.
  Missing credentials, empty/missing trajectory inputs, partial checkpoint loads
  and failed GPU allocation must produce explicit diagnosed blockers.
  Failed readiness leaves research stopped, retrieves any useful diagnostic
  artifacts, cleans up test resources and records preparation spending before a
  retry. An empty or unpriced trace fails readiness even if inference succeeds.

  Verify deadline/finalization and cleanup behavior with mocked commands before
  trusting a changed run configuration. Existing SDK coverage lives in
  `src/sdk-scaffold/tests/test_tabpfn.py`, `test_resources.py` and `test_openrouter.py`.
  If runtime behavior changes, add focused fixture coverage at
  `src/sdk-scaffold/tests/test_contrastive_rl.py`, following the SDK's test-first
  contribution rules. No live AWS/RunPod mutation belongs in those tests.

- [ ] **3. Execute the bounded pilot (R1–R4; depends on 2 and launch inputs).**

  Keep the question open and the operator's interventions recorded. The following
  are evaluation expectations and time allocations, not a prescribed method:

  | Research time | Expected progress |
  |---|---|
  | 0–1 h | Read the supplied brief/source, run a baseline, identify what directed quantity is being modeled |
  | 1–2 h | Compare a few parameterizations conceptually and select a tractable experiment |
  | 2–4.5 h | Implement one candidate and compare with the original under matched data/compute |
  | 4.5–5 h | Validate directionality and inspect failures; repeat a seed if feasible |
  | 5–6 h | Retrieve evidence, reproduce the small result, finish the report and clean up |

  The agent should distinguish asymmetric scores, conditional probabilities and
  time-reversed dynamics: a symmetric score does not itself imply equal normalized
  conditionals. Ask it to state its target and assumptions. Retain the freedom to
  choose its geometry and justify that choice intuitively and mathematically.
  Any discussion of existing methods should compare with the closest prior work.

  Evaluate on a small held-out setting with demonstrable directional structure,
  rather than inferring success from a visually asymmetric embedding. A small
  diagnostic can use known directed reachability/costs; distinguish it from a CRTR
  domain result. Keep compute, seeds, data splits and parameter counts visible.
  Record directional ranking/reachability performance and downstream solve rate
  or search effort when available. An arbitrary score difference is not enough.

  Required pilot evidence: the original baseline result, candidate definition and
  rationale, matched comparison or a precise reason it was blocked, configs,
  commands, seeds, logs, source changes and result files. Preserve the reference
  scaffold's required output format; the post-run assessment may be a brief memo.
  A negative or incomplete scientific result remains useful if supported honestly.
  No claim of seed robustness or broad superiority from a single short comparison.

- [ ] **4. Export, analyze and close (R3, R4; depends on 3).**

  Prove the export path during readiness, before the research run. For OpenClaw,
  use `utils/export-run.sh`. That exporter and `utils/make-blacklist.sh` assume
  OpenClaw stores; they are not an SDK-session exporter. For SDK mode, establish a
  run-filtered Langfuse/model-tool export and a readable event timeline on the box,
  including required session data, with credentials scrubbed before transfer.
  Build the on-box literal blacklist from the actual SDK service environment and
  any other delivered secret stores; `/etc/crux-run.env` is outside the legacy
  blacklist builder's sources. Reuse `utils/scan-secrets.py` for counts-only checks.
  Include a seeded non-vendor secret in an export fixture to verify scrubbing;
  block transfer on any remaining hit. Raw SQLite/session data, raw telemetry and
  the blacklist stay on the box and outside this repo.

  Save only scrubbed exports under ignored `runs-export/contrastive-rl-<run-id>/`.
  Preserve prompts, code/config hashes, per-call model/provider identity,
  usage/cost, tool events, experiment commands, results and intervention timestamps.
  Keep missing telemetry and estimated costs explicit. Report time/spend spent on
  setup, literature/theory, coding, compute, waiting/retries and writing; identify
  time to first baseline and first interpretable comparison. Link each important
  conclusion to a trace event and artifact. Save a concise findings memo there.

  Separate two verdicts: **environment readiness** (blocked / usable with fixes /
  ready for a longer run) and **scientific signal** (unsupported / negative /
  promising). A novel result is not required to judge the environment usable.
  Prioritize a few concrete fixes for the next run, grounded in evidence.

  Final checks: result files retrieved; run-owned pods terminated; cleanup status
  and provider inventories checked; spend reconciled; scrubbed evidence verified;
  EC2 stopped/terminated after export with retained disk/IP resources accounted
  for. Revoke run keys after collection and shutdown. Failed cleanup remains an
  operator action, never a successful completion claim.

## Launch inputs and ownership

| Input or action | Owner | Status |
|---|---|---|
| Research question/context | User | Supplied |
| CRTR source | User | Supplied as ZIP; no additional source inputs needed |
| Reference pilot configuration and selected model | Operator | Recorded in pilot/INPUTS.md; Sonnet 5.5/high default |
| Research budgets | User | Confirmed: $100 agent inference and $30 RunPod |
| AWS profile/session, region, SSH access | User/operator | To supply securely |
| OpenRouter credential | User | Supplied and read-only access verified; provider limit remains $250; run inference budget is $100 |
| RunPod credential | User | Supplied; stored in ignored mode-600 bundle; read-only API access verified, funding/compute unverified |
| Existing tracing/cost service access | Operator | Verify; identify owner if unavailable |
| Input bundle, readiness evidence and launch | AS/operator | Next execution work |
| Scrubbed trace analysis and recommendation | AS/operator | After pilot |

Runtime-dependent questions—dependency resolution, GPU size, baseline throughput,
useful step/evaluation counts and the agent's chosen method—are intentionally left
for readiness or the pilot. Record the baseline selection before paid preparation;
resolve remaining access blockers
before the research clock starts.
