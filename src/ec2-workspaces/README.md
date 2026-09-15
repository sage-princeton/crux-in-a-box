# Workspace provisioning

Run these scripts locally from `src/ec2-workspaces/`.

## Configuration

Copy `placeholders-base.txt.example` to `placeholders-base.txt` and fill in
the AWS, controller and agent settings. Set `AGENT_PLATFORM` to `codex` or
`claude`; an omitted platform selects Codex.

| Platform | Model | Effort | API key |
| --- | --- | --- | --- |
| `codex` | `CODEX_MODEL` | `CODEX_REASONING_EFFORT`: `minimal`, `low`, `medium`, `high` | `OPENAI_API_KEY` |
| `claude` | `CLAUDE_MODEL` | `CLAUDE_EFFORT`: `low`, `medium`, `high`, `xhigh`, `max` | `ANTHROPIC_API_KEY` |

Model, effort and version pins are required. Both provisioning entry points
validate the selected platform's settings before creating resources.
Each workspace receives a copy in `placeholders-<slug>.txt`.

Copy `run-secrets-base.json.example` for Codex or
`run-secrets-claude-base.json.example` for Claude to `run-secrets-base.json`.
Fill in the provider API key and set file mode 600.
Keep configuration and secrets in the gitignored files.

## Create a workspace

```bash
./make-new-workspace.sh <slug> --dry-run
./make-new-workspace.sh <slug>
```

The script checks prerequisites, creates the AgentRQ workspace, writes its
configuration and secrets, and provisions the EC2 instance. Each instance
requires an available Elastic IP allocation.

Use `--base-config <file> --base-secrets <file>` to select separate base files.
For an existing AgentRQ workspace, supply its ID and token in the per-run
secrets file and run:

```bash
./provision-workspace-aws-resources.sh --secrets <file> <config>
```

### Claude settings

Example configuration:

```ini
AGENT_PLATFORM=claude
CLAUDE_MODEL=claude-opus-4-6
CLAUDE_EFFORT=high
CLAUDE_VERSION=2.1.272
CLAUDE_ACP_VERSION=0.77.0
ACP_GATEWAY_VERSION=0.2.17
```

Select a model available to the Anthropic API key. Supported effort levels
depend on the model; Claude may reduce an unsupported level.

`install-run.sh` installs Node 22, the Claude CLI and `claude-agent-acp`.
The adapter uses the installed CLI through `CLAUDE_CODE_EXECUTABLE`.
Model and effort are stored in `/home/ubuntu/.claude/settings.json`.
Effort is supplied through `CLAUDE_CODE_EFFORT_LEVEL`, including `max`.

The settings file has mode 600 and contains the API key and Langfuse credentials.
`configure-run.sh` installs the standalone Claude Stop hook. It runs a paid
probe before starting the gateway, requiring an answer and a newly processed
hook turn. The source secrets bundle is deleted on the instance.

See the [adapter source](https://github.com/agentclientprotocol/claude-agent-acp)
and [Claude model configuration](https://code.claude.com/docs/en/model-config).

## Tracing

Both platforms attach these Langfuse metadata fields:

| Field | Value |
| --- | --- |
| `workspaceId` | AgentRQ workspace ID |
| `runSlug` | Provisioning slug |
| `agentPlatform` | `codex` or `claude` |
| `configuredModel` | Model selected at provisioning |
| `configuredEffort` | Effort selected at provisioning |

Tags are `workspace:<id>`, `run:<slug>` and `platform:<platform>`.
The environment is the run slug; the session ID is the native conversation ID.
The generation's `model` field records the transcript model.
Configured effort records the provisioning request, including any level the
model reduces. It does not track later session changes.

Claude receives metadata through `CC_LANGFUSE_METADATA`.
Codex uses its tracing plugin configuration.

Verify delivery after a dashboard task; allow for ingestion delay.
A successful hook log confirms processing only. To inspect propagated metadata
on observations, use `/api/public/v2/observations` with the `metadata` and
`trace_context` field groups.

## Tool permissions

New workspaces enable AgentRQ YOLO mode (`allowAllCommands=true`). Dashboard
and agent-created tasks inherit this setting. API callers creating human tasks
must include `allowAllCommands=true` in the task payload.

The gateway forwards tool permission requests to AgentRQ for approval.
Codex's `read-only` session-mode log is expected with this arrangement.
Claude uses its default permission mode and sends requests through ACP.
The workspace setting is under Settings → Automations → YOLO Mode (Execute All).

## Local checks

Run from the repository root:

```bash
for script in src/ec2-workspaces/*.sh; do bash -n "$script"; done
shellcheck -x -e SC2029,SC2088 src/ec2-workspaces/*.sh
```

The `Workspace checks` workflow runs these checks on pull requests and pushes
to `main`, plus whitespace and secret scans. It also supports manual runs.
Python 3 is required for the secret scan.
ShellCheck excludes intentional SSH expansion (SC2029) and quoted remote
paths containing a tilde (SC2088).

Each deployment requires a live dashboard task and Langfuse delivery check.
CI does not provision instances.

## Teardown

`teardown-workspace-aws-resources.sh` terminates the instance, releases its
Elastic IP and removes the SSH alias. It retains the AgentRQ workspace,
shared security group, key pair and IAM resources.
