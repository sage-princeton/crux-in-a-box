# Run Boxes

The Invoker uses the native Python `crux` CLI to validate a declarative Run,
create its resource graph, assemble a bundle, configure an idle Box and start the
Run explicitly. AgentRQ controller workspaces are resources distinct from Boxes.

## Invoker setup

Use Python 3.11+, OpenSSH and an authenticated, explicitly named AWS profile.
From the repository root, install the Invoker in a virtual environment outside
source, then activate it:

```bash
python3 -m venv ~/.local/share/crux-invoker
~/.local/share/crux-invoker/bin/pip install ./devops
source ~/.local/share/crux-invoker/bin/activate
aws sts get-caller-identity --profile YOUR_PROFILE
```

Copy [run.example.json](run.example.json) outside the checkout. Set a unique Run
name, your profile/account/region and the existing subnet, security groups, key
pair and instance profile. Select an Ubuntu 24.04 image compatible with the
instance type. The profile must be authenticated before cloud operations; use
SSO reauthentication if it expires. The Box profile needs access to the shared
SSM parameter (default `/crux/system/env`) containing the Langfuse/system settings.
SSH ingress and a reachable public address must already be available.

Keep the Run JSON, mode-600 credential JSON, SSH private key, deployment receipt,
bundle and private logs outside source. Only provider credentials and per-Run
service keys belong in the Run secret file; unselected provider credentials and
AgentRQ tokens are withheld from the Agent. Other keys are delivered to it.
Never put an Invoker-only controller root token in that file.

## Deployment sequence

```bash
crux plan ~/crux/research/run.json
crux preflight ~/crux/research/run.json --state ~/crux/research/state.json
crux provision ~/crux/research/run.json --state ~/crux/research/state.json \
  --source "$PWD" --secrets ~/crux/research/secrets.json --ssh-key ~/.ssh/crux.pem
crux health ~/crux/research/run.json --state ~/crux/research/state.json --ssh-key ~/.ssh/crux.pem
crux start ~/crux/research/run.json --state ~/crux/research/state.json --ssh-key ~/.ssh/crux.pem
```

`plan` checks the schema/graph without network access. `preflight` also checks
provider access. `provision` validates credentials and assembles inputs before
creating resources; it then configures the Scaffold. `create`, `bundle` and
`configure` expose those stages separately. Configuration runs live model and
sandbox probes, verifies the systemd unit and leaves Run services stopped.
`health` checks the recorded deployment and unit without starting it. Resolve
any drop-in placeholders before `start`. `start` dispatches the production
systemd unit; use Scaffold traces, gates and artifacts to establish Run success.
An SDK service can finish quickly and become inactive after successful work.

SSH requires a trusted host key or CA in OpenSSH configuration; `--known-hosts`
selects an explicit trusted file. For a new Box, authenticate its host fingerprint
through your AWS administrative channel before connecting. There is no automatic
host-key acceptance or deletion. If that trust is not yet installed, configuration
fails with the resources recorded; install trust and retry `configure` with the
same receipt. Private logs are next to that receipt.

A bundle records source commit, local tracked diff hash and each file hash. Only
tracked production components and the selected drop-in are included; private
config/cache paths and symlinks are rejected or excluded. A bundle checksum binds
configuration and later start/stop to the same deployment. Stop an active Run
before reconfiguration. Existing Run workspace and state survive configuration.
`ciab-devenv` consumes this same production bundle and Box setup, with local
service connections and an OrbStack transport.

## Scaffold selection and dependencies

| Scaffold | Run fields | Service |
| --- | --- | --- |
| Agents SDK | `type=sdk`, `platform=openai-agents` | `crux-sdk-run` |
| Codex on AgentRQ | `type=agentrq`, `platform=codex` | `crux-acp-gateway` |
| Claude on AgentRQ | `type=agentrq`, `platform=claude` | `crux-acp-gateway` |

Set `model`, `effort`, `provider` (`direct` or `openrouter` for ACP) and a
source-relative `drop_in`. SDK inputs include `scaffold.toml`; optional
`provision.json` declares Run software ([contract](provisioning.md)). The SDK
currently supports its direct OpenAI provider. Provider adapters can extend
Scaffold configuration without changing resource orchestration.

AgentRQ Scaffolds also set `configuration.control_mcp_base` to the controller's
HTTPS origin. Add an `agentrq.workspace` resource with `url` and `root_token_env`
(the name of an inherited Invoker environment variable), then set
`configuration.workspace_resource` to its resource ID. Workspace ID/token are
resolved privately after creation. Choose `teardown=retain` when tasks/history
must outlive the Box. Existing workspaces use `ownership=external`,
`teardown=retain` and `config.workspace_id`. Alternatively supply existing
workspace credentials in the private Run secret JSON and omit `workspace_resource`.

CLI/ACP/plugin versions come from `scaffolds/{codex,claude}/package.json` and
`package-lock.json`; installation uses `npm ci`. Python bootstrap tools come
from `devops/requirements-bootstrap.txt`, SDK packages from its `pyproject.toml`.
The controller image is declared in `devops/ec2-control/compose.yaml`. Change
these consumed manifests, not per-Run configuration. Codex tracing mode/trusted
hook hash live in `scaffolds/codex/tracing.json`; test fixtures enforce a reviewed
tracing baseline when versions change.

Ubuntu security upgrades remain enabled. `needrestart` excludes the Run services
so updates cannot interrupt a turn. Restart them between Runs after inspection.

## Cleanup and migration

```bash
crux destroy ~/crux/research/run.json --state ~/crux/research/state.json --ssh-key ~/.ssh/crux.pem
```

Cleanup first stops the configured Run over SSH, leaving its Box reachable for
finalization, then removes grants/resources in reverse dependency order. A stop
failure blocks deletion; configured Boxes require `--ssh-key`. Retained assets and external resources survive; ownership changes block
mutations. Keep the immutable Run document and receipt until teardown succeeds;
retry with that same pair after failure. Start a new deployment with a new
Run/state file after teardown. Never reuse receipts to adopt existing Boxes.
See [resource contracts](../crux_devops/README.md) for explicit address retention
and extension points for auxiliary resources and account handoffs.

The three old workspace shell entry points now forward to `crux provision` or
`crux destroy` with these JSON/receipt arguments. Their old placeholder-file,
slug and implicit-start interfaces have been removed. Historical live deployments
have no new ownership receipt: do not adopt them by name. Finish them with their
original revision/runbook; use this interface for new deployments.

## Codex tracing

On Codex boxes the tracing plugin uploads a turn to Langfuse from Codex's `Stop`
hook, which fires only when the turn ends. A turn cut off by a gateway stop,
crash or reboot never gets one, so the gateway unit runs
`/usr/local/lib/crux/codex-flush-turns.py` after every exit (`ExecStopPost`) and
before every start (`ExecStartPre`). It uploads the last turn of each main
rollout that the plugin hasn't recorded, with its subagents nested under it, and
tags it `flush:gateway-stop` or `flush:gateway-start`. A rollout another live
codex still holds open is left alone. See what it did with
`journalctl -u crux-acp-gateway | grep codex-flush`, or list what it would upload
with `sudo -iu ubuntu python3 /usr/local/lib/crux/codex-flush-turns.py --plugin <dist/index.mjs> --reason manual --check`.

`CODEX_TRACE_MODE=live` (the default) shows a turn in Langfuse while it runs
and exports Codex's reasoning summaries with each model generation. Codex is
configured with `model_reasoning_summary = "auto"`, which selects the most
detailed summarizer available for the model. Codex writes these summaries to
its rollout; they are not the model's private reasoning tokens. The legacy
`stop-hook` mode uses the Langfuse plugin and does not export reasoning
summaries.
Langfuse v4 never updates an observation it has stored, so instead of the
plugin's `Stop` hook a service, `crux-codex-live-trace`, reads the rollouts every
30s and sends each observation once, as soon as it is complete:

- a `Codex Turn started` event with the turn's input, when the prompt is written
- an `LLM` generation per model response, with its tool calls and token usage
- a tool span per call, once its output is written
- anything else the turn recorded, as a span (under code mode, each shell command
  `exec` ran) or, for a line the exporter does not recognise, an event; the root
  lists unrecognised line types in `codex.unrecognized_types`, and the service logs them
- the `Codex Turn` root, with the input and output, when the turn ends

Ids come from the thread, turn and call, so children point at their root before
it arrives and nothing is sent twice. Until the root arrives, the turn shows in
the trace detail, session and observations views, not in lists built from root
observations. Subagent turns nest under the turn that was running when they
started. In this mode the gateway unit runs `codex-live-trace.py --once
--finalize` instead of the plugin flush, which ends a killed turn with a
`WARNING` root. Live mode doesn't install the plugin, since `codex plugin add`
re-enables it and every turn would be traced twice; provisioning fails if its
`Stop` hook still runs. Follow the exporter with `journalctl -u crux-codex-live-trace`.

The exporter is the `live_trace/` package, installed to `/usr/local/lib/crux/`
with its `codex-live-trace.py` entry point. Only `live_trace/codex.py` knows the
Codex rollout format; the turn model, the Langfuse observation schema, the ledger
of sent ids and the OTLP sink are shared. Tracing another agent means one more
`TranscriptSource` subclass and entry point; `live_trace/__init__.py` describes
the design.
