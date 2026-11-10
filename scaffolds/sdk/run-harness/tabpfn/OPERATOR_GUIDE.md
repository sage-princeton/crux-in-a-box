# TabPFN SDK research run

This drop-in uses `google/gemini-4-argon` at high reasoning effort through
OpenRouter for the PI, assistants, judges and Codex workers. It reproduces the
PFN harmful-shift research task with a six-hour,
$200 model / $100 RunPod envelope. It replaces OpenClaw heartbeats, CLI review
scripts and file-based operator polling with SDK context/resource injection,
background workers, isolated PDF judging and durable Slack steering. The PI and
assistants have shell access; Codex uses full access. No token, turn, iteration,
delegation-depth or global concurrency limit is configured. The three research
assistants and eight named Codex workers each hold one outstanding conversation
per name. Native Codex fan-out has no configured concurrency/depth cap. The Codex workers use
`prompts/codex-worker.md` in place of Codex's built-in system prompt, which is
written for an interactive session with a person, and read `AGENTS.md` once, from
their standing context.
The SDK fixes Codex request and stream retry limits at 10 for every worker. A turn that still fails on a transient provider error
continues on the same thread after 60 s and then 300 s; a brief that still fails
reports the error kind, turn attempts and last error to the PI.

Research advances after an Accept (Overall 5/6 or 6/6). The loop also moves to
finalization at one hour remaining, $180 model spend, or $90 RunPod spend. These
are reserves within the same envelope, not additional run caps. Reserve crossing
cancels/checkpoints the in-flight iteration and starts the finalization prompt;
interrupted delegate conversations remain available for explicit PI resumption. The six-hour deadline cancels unfinished
work, including a judge or coding call. Reported spending is checked during work
every 15 seconds, subject to provider caching/read time. Langfuse/RunPod ingestion
lags can cause overshoot; this is not a provider billing guarantee. Use dedicated
run credentials/accounts and provider-side controls when an invoice ceiling is
required. A missing/stale cost measurement remains explicitly unknown.

`render_prompts.py` writes `PROMPTS.md`: the drop-in's prompt text, one block
per file, then the setup section the scaffold generates, for reading and rewriting
(for example in Google Docs). The file is not committed; generate a current copy
from `scaffolds/sdk`:

```bash
.venv/bin/python run-harness/tabpfn/render_prompts.py
```

## Dependencies and deployment

Use a worktree containing this committed change: production stages its `HEAD`.
The SDK shares one operator-selected model/effort across PI, assistants and Codex.
Select a Responses/compaction-capable model supported by the installed Codex SDK.
Both review gates use a three-model panel through OpenRouter instead:
`google/gemini-4-argon`, `anthropic/claude-opus-5.5` and `openai/gpt-6-astra`, at the
run's effort. The OpenRouter key must reach all three; provisioning probes the run
model and every panel model, so do not assume model access. A panel review costs
roughly $5–10.

1. Authenticate AWS explicitly:
   ```bash
   aws sts get-caller-identity --profile crux-2-acct-admin-access
   ```
   If it fails, reauthenticate with `aws configure sso --use-device-code` before
   continuing. Do not fall back to another credential source.
2. Reuse the workspace fleet's Langfuse project, or create a project and install
   its keys in the existing `/crux/system/env` SSM configuration. See
   [workspace provisioning](../../../../devops/ec2-workspaces/README.md). Use that same
   project for tracing and cost tracking; confirm the selected model has pricing.
3. Deploy or reuse the shared [Langfuse cost Lambda](../../../../monitoring/costs/langfuse/README.md).
   Load `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` and a
   strong `COST_TRACKER_TOKEN` from your private secret store, then from repo root:
   ```bash
   ./monitoring/costs/langfuse/deploy.sh --profile crux-2-acct-admin-access --region us-east-1
   ```
   Save the printed HTTPS `/cost` URL as `COST_TRACKER_URL`. The bearer token can
   read totals for the whole project, so use a research-specific project if needed.
4. At [Slack apps](https://api.slack.com/apps), create an app from
   `slack-app-manifest.yaml`, install it and invite `@crux-tabpfn` into a dedicated
   run channel. Save its bot token privately. Record the channel ID and the human
   operator's Slack user ID. The MCP server's conversation cache needs all listed
   read scopes; reinstall after scope changes. No Events API or Socket Mode is
   needed. The manifest does not create the channel; create/invite it explicitly.
5. Create a RunPod key that can list/create/delete pods and read billing. Use a
   unique lowercase run slug and a dedicated run allocation. Each pod must carry
   `CRUX_RUN_SLUG` exactly matching it. No email, Together, Hugging Face, Prior Labs,
   refine.ink or experiment-LLM credentials are required or delivered.

## Provision the workspace

Copy `placeholders.txt.example` and `secrets.json.example` to `placeholders.txt`
and `secrets.json` beside this guide, fill their values, and keep both mode 600.
These operator files are gitignored. Use the existing fleet/key-pair settings;
the supplied model settings are:

```ini
AWS_PROFILE=crux-2-acct-admin-access
AWS_REGION=us-east-1
AGENT_PLATFORM=openai-agents
MODEL_PROVIDER=openrouter
OPENAI_AGENTS_MODEL=google/gemini-4-argon
OPENAI_AGENTS_REASONING_EFFORT=high
DROP_IN_PATH=scaffolds/sdk/run-harness/tabpfn
INSTANCE_TYPE=m7i.2xlarge
ROOT_DISK_GB=100
```

Use an existing `KEY_NAME`; remove an inherited `ELASTIC_IP_ADDRESS` unless that
specific address belongs to this run. SDK runs need no AgentRQ/control box setup.
Model and effort are run environment settings: provisioning maps
`OPENAI_AGENTS_MODEL` and `OPENAI_AGENTS_REASONING_EFFORT` to `CRUX_MODEL` and
`CRUX_REASONING_EFFORT` for every agent and reviewer. `scaffold.toml` selects the
OpenRouter Responses endpoint and `OPENROUTER_API_KEY` for the SDK and Codex.
No provider-specific route or preset is required; OpenRouter account settings
control routing. Check the actual route and pricing in usage records.
Commit changes before provisioning;
uncommitted changes are not staged on the box.

The PI uses provider-independent `compaction`: at 650,000 estimated tokens, it
summarizes older exchanges and keeps roughly 20 recent items, preserving complete
tool exchanges and the full SQLite history. Item count does not trigger compaction.
`context_window_tokens = 1048576` records Argon's supported window; `image_tokens = 1120`
budgets each image separately from its encoded payload. The token trigger is an
experimental calibration: the earlier Argon sample's 1.336x input-to-estimate ratio
would imply about 868,000 input tokens, leaving roughly 180,000 for output and
additional uncertainty. The v2 pilots peaked below 240,000 actual PI input tokens,
so they do not validate this larger threshold. Compare full-request estimates with
provider usage as history grows; the estimate is not a provider-token fit guarantee.
Codex workers manage their own context separately.

Fill `secrets.json` with **only** `OPENROUTER_API_KEY`,
`SLACK_BOT_TOKEN`, `COST_TRACKER_URL`, `COST_TRACKER_TOKEN` and `RUNPOD_API_KEY`.
Other non-provider base secrets are inherited by provisioning, so do not reuse a
bundle containing the omitted services. Langfuse keys come from fleet SSM.

For direct OpenAI instead, remove `[runtime.model_provider]`, set
`MODEL_PROVIDER=direct` and an OpenAI model, and supply `OPENAI_API_KEY`.

From the repository root, choose a unique run slug and run:

```bash
./devops/ec2-workspaces/make-new-workspace.sh tabpfn-sdk-<unique-suffix> \
  --base-config scaffolds/sdk/run-harness/tabpfn/placeholders.txt \
  --base-secrets scaffolds/sdk/run-harness/tabpfn/secrets.json --dry-run
```

Repeat without `--dry-run` to provision.

`provision.json` installs LaTeX/latexmk, PDF extraction/rendering, venv and transfer
utilities. The service's software checks run at provisioning and every start.
The SDK/Codex probes use real inference. Provisioning installs `crux-sdk-run`
without starting it. Research libraries belong in the agent's project venv and
on its GPU pods, with exact versions recorded by the research run.

## Prepare and start

SSH as `ubuntu` using the run slug. With the service stopped:

```bash
cd /srv/crux-run/run-harness
python3 prepare.py --channel C0123456789 --operator U0123456789 --after-ts "$(date +%s).000000"
cd workspace/paper
latexmk -pdf -interaction=nonstopmode -halt-on-error paper.tex
cd /srv/crux-run/run-harness
sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
  -p EnvironmentFile=/etc/crux-run.env \
  /opt/crux-sdk-scaffold/.venv/bin/python -m crux_scaffold check \
  --drop-in /srv/crux-run/run-harness --state-dir /srv/crux-run/state
sudo systemctl start crux-sdk-run
journalctl -u crux-sdk-run -f
```

Replace both Slack IDs. `prepare.py` fetches the official
[NeurIPS 2025 template](https://media.neurips.cc/Conferences/NeurIPS2025/Styles.zip),
checks its pinned SHA-256, saves `workspace/templates/paper_template.zip` and
installs the style beside the included anonymous paper skeleton. `--template PATH`
uses a previously downloaded archive with the same checksum. It refuses to rewrite
an already-resolved run. Use a fresh staged copy for another run. The start cursor
excludes earlier channel messages; post steering as new top-level messages after
that timestamp. Only the configured operator ID is delivered automatically. Slack
edits/replies are not new top-level steering. Normal progress uses the bot's channel.

The PI verifies token-free public [TabPFN v2](https://huggingface.co/Prior-Labs/TabPFN-v2-clf)
weights and white-box access at hour zero, explicitly selecting v2 rather than a
gated default. Package versions, checkpoint hashes, dataset versions and licenses
are research outputs. No hosted Prior Labs prediction API is used.

## Records, resumption and shutdown

`workspace/PLAN.md` carries current scientific/budget state; `LOG.md` is historical.
The review gate checks the abstract, main-body aux label and PDF readability, then
snapshots only `paper/paper.pdf` under the neutral alias `submission.pdf`. The
reviewers have only built-in read/list/PDF tools, no author context, shell, MCP or
previous reviews. Three models review independently and the paper passes when two
recommend acceptance (Overall 5 or 6). The PI receives all three reviews in full,
numbered Reviewer 1–3 without model names. The structured result, including which
model wrote which review, is saved under `/srv/crux-run/state/tabpfn/reviews/`;
`workspace/reviews/` mirrors it without the model names. The PDF, rubric and panel
hash binds cached verdicts; unchanged papers cannot be rerolled. A panel in which a
reviewer failed still decides but isn't cached, so the next request reviews again.
`REVIEW_STATUS.json` contains the latest review's PDF hash, archive path and
complete verdict, including the original reviewer feedback. The PI receives this
file before PLAN.md in its refreshed research context on every model call,
independent of conversation compaction. Each new review replaces the status;
historical reviews remain in `reviews/`. The PI's 120,000-character workspace
context budget allows roughly 60,000 characters for the review status before
PLAN.md: three reviews of the usual 10,000–14,000 characters fit.
Longer files carry the existing truncation notice; the archive retains the full
feedback.
The final gate checks required
deliverables and evidence hashes and binds the completion report to the final PDF
and review. Semantic evidence quality, page-layout correctness and accessibility
still require reviewer/agent inspection; mechanical checks are not scientific proof.

`RUN_OUTCOME.json` distinguishes acceptance from a completed package at the resource
reserve. A rejected paper can be packaged honestly once the reserve is reached.
Hard exhaustion exits 3 with incomplete work preserved and no fabricated completion.
The final gate actually runs `bash reproduce.sh`, records its output in
`REPRODUCTION_STATUS.json`, requires exit 0, and rechecks evidence hashes and the
reviewed PDF afterward. It uses the remaining wall-clock budget; a timed-out
reproduction is unfinished work, not a passing package.

All loop exits (including interruption) request deletion of only pods whose current
ownership marker exactly matches `RUN_SLUG`. This also deletes pod-local storage:
the agents must continuously checkpoint/copy important results to the workspace.
Inspect `CLEANUP_STATUS.json` and the RunPod console after stopping; API failures,
unmarked pods, separate network volumes and serverless resources are outside an
automatic cleanup guarantee. The run must use marked pods, not those other services.
The EC2 run box persists for evidence collection and is outside the $100 RunPod cap.

Stop with `sudo systemctl stop crux-sdk-run`; restart the same unit to resume the
saved phase/session. Keep the whole `/srv/crux-run/state` directory (including
SQLite, steering state, resource inventory and review cache) with the workspace.
Time includes downtime; restarting does not reset the six hours or budget. A
restarted run must recreate terminated GPU work from checkpoints. Inspect the
journal, state and Langfuse environment/session named by the run slug. Preserve
scrubbed artifacts before using the fleet's workspace teardown flow.

Local acceptance uses `ciab-devenv` with its Slack/RunPod fixtures, local Langfuse
and the production systemd unit. PR-specific bounded rehearsal scripts and evidence
belong in the PR description and temporary directories, not this example.

The scaffold loads personas and `context` files once as stable instructions. It
refreshes the bounded `PLAN.md` summary in a temporary user prefix on every model
call, alongside scaffold workflow and resource state. Keep detailed logs, review
verdicts and experiment outputs in their existing files; link them from PLAN.md.

## Delegate communication

`interactive_delegates` enables PI steering, interruption, resumption and `request_progress` for the SDK
research assistant and Codex workers. Delegates can ask questions and report progress, but cannot interrupt
or schedule an active PI turn. The PI uses `delegate_inbox(wait_seconds)` for both waiting and collection;
any new mail or uncollected completion/failure/interruption releases the wait. The response includes all ready
results, and other delegates continue running. Interactive mode no longer exposes named `_result` tools.
Keep `delegates.sqlite`, `sessions.sqlite` and `codex/` together with run state. A restart
marks running tasks interrupted; the PI chooses whether to resume the same conversation or collect its
partial result. Codex uses native steering/interruption and a task-bound MCP server for upward mail.
Interruption waits for harness cleanup; independently launched training processes or pods still need their
recorded cleanup commands. Langfuse `delegate:*` events link dispatch, messages, delivery, and results by task ID.
A delivery receipt means input handoff, not proof of model compliance. Concurrency limits are unchanged.

The shared operating context, role prompts and finalization instructions reinforce
process ownership when stopping work. This is cooperative guidance for agents on
the same host and Unix account, not OS-level process isolation.
