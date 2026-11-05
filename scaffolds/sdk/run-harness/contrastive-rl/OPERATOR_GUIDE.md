# Contrastive RL SDK pilot

Use google/gemini-4-argon, high effort, through OpenRouter. The unattended pilot
has six hours, $100 total agent inference and $30 RunPod. Leave the OpenRouter
key's provider limit unchanged. All agents receive the $100 instruction; the TOML
also applies that budget and reserves the last hour/final 10% for finishing.

The Codex workers use `prompts/codex-worker.md`, the TabPFN drop-in's worker
prompt, in place of Codex's built-in system prompt, which is written for an
interactive session with a person.
Codex retries provider failures 10 times inside a turn (`request_max_retries`,
`stream_max_retries`). A turn that still fails on a transient provider error
continues on the same thread after 60 s and then 300 s; a brief that still fails
reports the error kind, turn attempts and last error to the PI.

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

## Operator files

placeholders.txt and secrets.json beside this guide are the operator inputs.
Both are ignored; keep them mode 600. The secrets file needs your
OpenRouter and RunPod keys, `SLACK_BOT_TOKEN`, and the existing cost-tracker URL/token. AWS
credentials stay in the operator environment and are not delivered to the agent.
Shared Langfuse credentials come from SSM /crux/system/env, using the CRUX project.

After checking out this branch, copy the .example files and fill their values:

    cp scaffolds/sdk/run-harness/contrastive-rl/placeholders.txt.example \
      scaffolds/sdk/run-harness/contrastive-rl/placeholders.txt
    cp scaffolds/sdk/run-harness/contrastive-rl/secrets.json.example \
      scaffolds/sdk/run-harness/contrastive-rl/secrets.json
    chmod 600 scaffolds/sdk/run-harness/contrastive-rl/placeholders.txt \
      scaffolds/sdk/run-harness/contrastive-rl/secrets.json

The config selects this directory with
DROP_IN_PATH=scaffolds/sdk/run-harness/contrastive-rl. Omit RUN_SLUG in the base
placeholders; make-new-workspace.sh sets it. AWS access must permit provisioning
and reading the shared SSM settings; the cost-tracker service must be available.

Create a Slack app from `slack-app-manifest.yaml`, install it and invite
`@crux-contrastive-rl` to a dedicated run channel. Store its bot token only in
`secrets.json`; record the channel ID and the human operator's Slack user ID.
All listed read scopes are needed by the MCP conversation cache. Reinstall the
app after scope changes. No Events API or Socket Mode is needed.

## Create the workspace

The full supplied CRTR input is checked in under workspace/crtr, including all
103 original files, its MIT license, example datasets and checkpoints (about
123 MB unpacked). workspace/SOURCE.json records its revision, original archive
SHA-256 and each file's SHA-256. No ZIP, download or source preparation is needed.
Full training datasets beyond the supplied examples remain optional external
inputs; see crtr/README.md and inspect the available data before choosing a task.

The existing scripts stage HEAD. Commit any harness changes before provisioning.
The example uses the committed workspace; WORKSPACE_DIR is only needed when you
intentionally want to overlay a separate local workspace.

With the intended AWS session exported, run from the repository root:

    devops/ec2-workspaces/make-new-workspace.sh contrastive-rl-trial \
      --base-config scaffolds/sdk/run-harness/contrastive-rl/placeholders.txt \
      --base-secrets scaffolds/sdk/run-harness/contrastive-rl/secrets.json --dry-run

Choose a unique run slug in place of contrastive-rl-trial. Run the same command
without --dry-run to provision. It installs the SDK and run software and stages
the committed drop-in, including the CRTR inputs. Credentials stay outside the
workspace. Provisioning delivers credentials and probes SDK/Codex inference into Langfuse. It leaves
crux-sdk-run stopped.

## Prepare and start

On the new box as ubuntu, with the service stopped, resolve the Slack channel,
operator and starting timestamp, then validate the staged example:

    cd /srv/crux-run/run-harness
    python3 prepare.py --channel C0123456789 --operator U0123456789 --after-ts "$(date +%s).000000"
    cd workspace/paper
    latexmk -pdf -interaction=nonstopmode -halt-on-error paper.tex
    sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
      -p EnvironmentFile=/etc/crux-run.env \
      /opt/crux-sdk-scaffold/.venv/bin/python -m crux_scaffold check \
      --drop-in /srv/crux-run/run-harness --state-dir /srv/crux-run/state

Replace both Slack IDs. `prepare.py` fetches the official
[NeurIPS 2025 template](https://media.neurips.cc/Conferences/NeurIPS2025/Styles.zip),
checks its pinned SHA-256, saves `workspace/templates/paper_template.zip` and
installs the style beside the included anonymous paper skeleton. `--template PATH`
uses a previously downloaded archive with the same checksum. It refuses to rewrite
an already-resolved run; use a fresh staged copy for a new run. Post steering as new top-level messages
after the starting timestamp. Only the configured operator's messages are
delivered automatically; edits and thread replies are not new channel steering.
The PI posts launch, milestone, roughly hourly and completion updates to that
channel and escalates diagnosed blockers while continuing independent work.

Check the configured model is Argon and the probe traces have token usage and
prices in this run's Langfuse environment. The model budget is measured from
launch, so probe spending before launch is not counted against the $100. Then start:

    sudo systemctl start crux-sdk-run
    journalctl -u crux-sdk-run -f

The clock starts at research launch; downtime counts on resume. Source code lives
in workspace/crtr and SOURCE.json records its pin. The existing SDK research loop,
review gates and cleanup come from the TabPFN example; their registered type names
and state/tabpfn paths remain unchanged.
The PI's refreshed research context includes the complete latest
`REVIEW_STATUS.json` verdict before PLAN.md, independent of conversation
compaction. Each new review replaces the status; historical reviews remain in
`reviews/`. The PI's 64,000-character workspace context budget allows roughly
32,000 characters for the review before PLAN.md. Longer files carry the existing
truncation notice; the archive retains the full feedback.

## Evidence and shutdown

Progress and blockers go to Slack, PLAN.md, LOG.md and COMPLETION_REPORT.md. Preserve
workspace artifacts and the state directory together on the box, including
`steering.json` and the conversation SQLite store so replies are not redelivered
on resume. Stop with `sudo systemctl stop crux-sdk-run`; restart the same unit
without rerunning `prepare.py`. The loop requests
deletion only of RunPod pods marked with this run's exact RUN_SLUG. Retrieve
results before exit and inspect CLEANUP_STATUS.json and the provider console.

`RUN_OUTCOME.json` distinguishes acceptance from a completed package at the resource
reserve. A rejected paper can be packaged honestly once the reserve is reached.
Hard exhaustion exits 3 with incomplete work preserved and no fabricated completion.
The final gate actually runs `bash reproduce.sh`, records its output in
`REPRODUCTION_STATUS.json`, requires exit 0, and rechecks evidence hashes and the
reviewed PDF afterward. It uses the remaining wall-clock budget; a timed-out
reproduction is unfinished work, not a passing package.

The model allowance uses Langfuse-reported dollars. Before treating it as a
provider-dollar cap, verify that the cost tracker's prices match the actual route
and BYOK billing basis, including cache reads and reasoning. Reporting delays
and in-flight work can still overshoot the allowance; the OpenRouter key limit
is separate and is not modified by this example.

Keep trace exports local. Before transferring any session or telemetry records,
scrub and independently scan them on the box; never commit raw records or the
literal credential blacklist.

After evidence collection, use the existing
devops/ec2-workspaces/teardown-workspace-aws-resources.sh with the generated
placeholders-<slug>.txt to remove the workspace resources.
