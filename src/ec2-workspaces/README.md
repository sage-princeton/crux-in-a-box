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

**What the agent runs as is config, not a flag.** `CODEX_MODEL` and
`CODEX_REASONING_EFFORT` (`minimal|low|medium|high`) must both be set in
`placeholders-base.txt`; there is no default behind them, and
`make-new-workspace.sh` refuses to mint a workspace until they are. To run
boxes at mixed settings, edit the base file between boxes — each box's values
are copied into its `placeholders-<slug>.txt`, so what a box ran as stays
readable next to the box. The old `--model` / `--effort` flags are gone: the
common case was forgetting them, which produced a box at whatever the default
happened to be with nothing in the run record saying the choice was never made.

### Teardown a workspace

This will:

- Delete the AWS assets
- **Keep** the workspace in Agent RQ

**`teardown-workspace-aws-resources.sh`** — laptop. Terminates one box, releases its Elastic IP,
removes the ssh alias. Deliberately keeps the shared SG / key pair / IAM —
and the AgentRQ workspace, which outlives its box.
