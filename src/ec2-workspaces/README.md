<!-- FIXME: clean this up, becoming a mess -->

# `ec2-workspaces/` — Workspace-level scripts

## Usage

### Make a new workspace

This will:

- Create the AWS assets
- Create the workspace in Agent RQ

**`make-new-workspace.sh`** — laptop, and the one you normally want:
`./make-new-workspace.sh <slug>` mints the workspace, writes the per-box config and
secrets, and calls `provision-workspace-aws-resources.sh`. ~2 minutes. All AWS checks run _before_
the workspace is minted, so bad credentials cost nothing.

**What the agent runs as is config, not a flag.** Set `AGENT_PLATFORM` in
`placeholders-base.txt` and supply that platform's model and effort. Older
configs without `AGENT_PLATFORM` retain Codex behavior.

| Platform | Required model | Required effort                                              | Per-run API key     |
| -------- | -------------- | ------------------------------------------------------------ | ------------------- |
| `codex`  | `CODEX_MODEL`  | `CODEX_REASONING_EFFORT`: `minimal`, `low`, `medium`, `high` | `OPENAI_API_KEY`    |
| `claude` | `CLAUDE_MODEL` | `CLAUDE_EFFORT`: `low`, `medium`, `high`, `xhigh`, `max`     | `ANTHROPIC_API_KEY` |

Model and effort have no defaults. Both entry points validate the selected
platform's settings and version pins before creating resources. To run
boxes at mixed settings, edit the base file between boxes — each box's values
are copied into its `placeholders-<slug>.txt`, so what a box ran as stays
readable next to the box. The old `--model` / `--effort` flags are gone: the
common case was forgetting them, which produced a box at whatever the default
happened to be with nothing in the run record saying the choice was never made.

### Provision a Claude workspace

Copy `placeholders-base.txt.example` to `placeholders-base.txt`, fill in the
shared AWS/control settings, and set:

```ini
AGENT_PLATFORM=claude
CLAUDE_MODEL=claude-opus-4-6
CLAUDE_EFFORT=high
CLAUDE_VERSION=2.1.272
CLAUDE_ACP_VERSION=0.77.0
ACP_GATEWAY_VERSION=0.2.17
```

Choose a model your Anthropic API key can access. The model above illustrates
the format; it is not a default. The Claude CLI and adapter require Node 22,
which `install-run.sh` installs. Codex version and tracing-plugin settings
are ignored for Claude boxes.

Copy `run-secrets-claude-base.json.example` to `run-secrets-base.json`, fill in
`ANTHROPIC_API_KEY`, and restrict the file to mode 600. Then run:

```bash
./make-new-workspace.sh crux-claude-1 --dry-run
./make-new-workspace.sh crux-claude-1
```

To keep a separate Claude base alongside the existing Codex defaults, pass
`--base-config placeholders-claude-base.txt --base-secrets run-secrets-claude-base.json`.
Both files follow the same formats above; the default files are left intact.

The dry run performs read-only prerequisite checks. For an already-created
AgentRQ workspace, use `run-secrets-claude.json.example` with
`provision-workspace-aws-resources.sh --secrets <file> <config>` instead.

Claude boxes run `claude-agent-acp` through the same gateway as Codex. The
adapter uses the explicitly installed Claude CLI via `CLAUDE_CODE_EXECUTABLE`,
so the CLI probe and gateway use the same version. Model and effort live in
`/home/ubuntu/.claude/settings.json`; effort is supplied through
`CLAUDE_CODE_EFFORT_LEVEL`, including `max`, which is not a persistent
`effortLevel` setting. Supported effort levels depend on the chosen model;
Claude may reduce an unsupported level, so confirm the model's capabilities
when selecting `xhigh` or `max`.

The mode-600 Claude settings file also contains its API key and the shared
Langfuse credentials. `configure-run.sh` copies the existing standalone
`agentrq/claude/.claude/hooks/langfuse_hook.py`, enables its Stop hook, and
tags its tracing environment with the run slug. It does not install the
Langfuse plugin, whose prompt filtering is unsuitable for AgentRQ.

Both platforms also propagate `workspaceId`, `runSlug`, `agentPlatform`,
`configuredModel`, and `configuredEffort` as Langfuse metadata, with
`workspace:<id>`, `run:<slug>`, and `platform:<platform>` tags. These fields
capture provisioning settings; the generation's native `model` field records
the model reported by the transcript. `configuredEffort` is the requested
level, not a measurement of effective thinking or later session changes.
Native conversation IDs remain Langfuse session IDs. Claude receives the
metadata through `CC_LANGFUSE_METADATA`; Codex uses its tracing plugin config.

Before starting the gateway, provisioning runs a short paid Claude probe and
requires both a successful answer and a newly processed tracing-hook turn.
A failed model/auth call, silent hook, or stale success log fails provisioning.
This verifies transcript processing; verify delivery in Langfuse when checking
the first dashboard task. The source secrets bundle is deleted on the box.

The adapter integration follows its [published source](https://github.com/agentclientprotocol/claude-agent-acp)
and Claude's [model and effort configuration](https://code.claude.com/docs/en/model-config).

### Tool Permissions

New workspaces enable AgentRQ's built-in YOLO mode (`allowAllCommands=true`).
New dashboard tasks inherit this setting, and agent-created tasks use it as
their default. API callers creating human tasks must set `allowAllCommands=true`
in their task payload. Existing tasks retain their own setting.

The gateway is the unmodified, pinned npm package. For both platforms it forwards tool permission
requests to AgentRQ, which automatically approves them for YOLO tasks. Its
`read-only` session-mode log for Codex is expected: approvals are handled by AgentRQ.
There is no gateway patch or permission environment variable. The workspace
default is visible under Settings -> Automations -> YOLO Mode (Execute All).

Claude retains its default permission mode and sends approval requests through
ACP. The old local-channel YOLO FIXME does not require bypassing Claude's
permission mechanism: AgentRQ's workspace/task YOLO setting handles it.

### Local verification

Install Bash and ShellCheck, then run from the repository root:

```bash
for script in src/ec2-workspaces/*.sh; do bash -n "$script"; done
shellcheck -x -e SC2029,SC2088 src/ec2-workspaces/*.sh
```

The `Workspace checks` GitHub Actions workflow runs these checks on every PR
and push to `main`, plus whitespace and secret scans over changed files.
Python 3 is used for the secret scan.
It can also be run manually. ShellCheck excludes SC2029 (intentional local
expansion in SSH commands) and SC2088 (quoted remote tilde paths).

A live dashboard turn and Langfuse delivery check are required when deploying
a new box; CI checks shell syntax and static analysis without provisioning.

### Live Claude verification — September 15, 2026

Provisioned `crux-claude-1` from branch `ae-209-claude-provisioning-live`
(implementation commits `35cf8f5`, `230d2d7`), using separate Claude base
files. The workspace is left running:
[open AgentRQ](https://32-195-122-118.sslip.io/workspaces/0inLNjkTlB3/board),
or connect with `ssh crux-claude-1`.

- EC2 instance `i-07f050b3722ee1f0d`; workspace `0inLNjkTlB3`.
- Claude CLI `2.1.272`, Claude ACP `0.77.0`, gateway `0.2.17`;
  configured model `claude-opus-5`, effort `high`.
- Provisioning's paid CLI probe answered successfully and processed a new
  Stop-hook turn. The gateway connected and advertised Claude to AgentRQ.
- Task `0inLkItagDZ` read a fresh random input, multiplied 17 by 23,
  calculated SHA-256 of the product plus nonce, wrote `validation/result.json`,
  read it back, and completed. The operator independently compared the exact
  JSON against a locally calculated result. AgentRQ recorded the write as
  `auto_allowed`; no manual tool approval was needed.
- Automatic Langfuse traces `836f3eac72acf2fa2e4835b9468ae200` and
  `907407aafb6dae8bc59c9c300971e57d` contain `claude-opus-5` generations,
  read/write/shell tools, the expected reply, and environment `crux-claude-1`.
  Ingestion took about two minutes after each hook. A successful hook log
  means processing finished; allow for ingest delay and verify the trace in
  Langfuse before diagnosing missing delivery. Diagnostic transcript replays
  were also performed; the first automatic trace's ingestion timestamp
  predates those replays.
- After the task and ACP turn finished, the test gateway was restarted.
  AgentRQ reconnected and a follow-up wrote `validation/resumed.txt` with
  the saved digest. The service remained active with zero automatic restarts.
  Temporary hook diagnostics were removed and the deployed hook's SHA-256
  matched the repository copy.

### Live metadata verification — September 15, 2026

Workspace [crux-trace-effort-1](https://32-195-122-118.sslip.io/workspaces/0inRZtxmwMr/board)
is running `claude-opus-5` with configured effort `high`. Filter Langfuse by
environment `crux-trace-effort-1` or tag `workspace:0inRZtxmwMr`.

- Reasoning conversation: trace `d31e90bb49c9815e61079324ac699468`.
- File write/read/hash conversation: trace `384fd56f15944db05d8034b023d5ef67`.
- Arithmetic follow-up: trace `68bded4e1631efd03af4775364e5de87`, sharing the
  first conversation's session ID.

All 26 observations across these turns contain the five configured metadata
fields. Verification used `/api/public/v2/observations` with the `metadata`
and `trace_context` field groups; the legacy observations endpoint does not
include propagated trace metadata in each observation's metadata object.
The file contents and SHA-256 digest were independently verified.

### Teardown a workspace

This will:

- Delete the AWS assets
- **Keep** the workspace in Agent RQ

**`teardown-workspace-aws-resources.sh`** — laptop. Terminates one box, releases its Elastic IP,
removes the ssh alias. Deliberately keeps the shared SG / key pair / IAM —
and the AgentRQ workspace, which outlives its box.
