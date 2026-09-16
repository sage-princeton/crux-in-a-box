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

Set `MODEL_PROVIDER=openrouter` in a separate base config to use OpenRouter
with either agent. Set `CODEX_MODEL` or `CLAUDE_MODEL` to its full OpenRouter
model ID, and put `OPENROUTER_API_KEY` in the base secrets JSON. Omitting
`MODEL_PROVIDER` keeps direct OpenAI/Anthropic billing.

OpenRouter access alone does not establish Google Cloud billing. That requires
a configured Vertex AI BYOK account and a model served by Vertex. To require
that billing path, restrict routing to Vertex and disable shared-capacity
fallback in OpenRouter; verify the actual provider and BYOK usage before a run.

### Teardown a workspace

This will:

- Delete the AWS assets
- **Keep** the workspace in Agent RQ

**`teardown-workspace-aws-resources.sh`** — laptop. Terminates one box, releases its Elastic IP,
removes the ssh alias. Deliberately keeps the shared SG / key pair / IAM —
and the AgentRQ workspace, which outlives its box.
