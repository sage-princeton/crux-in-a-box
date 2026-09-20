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

### Teardown a workspace

This will:

- Delete the AWS assets
- **Keep** the workspace in Agent RQ

**`teardown-workspace-aws-resources.sh`** — laptop. Terminates one box, releases its Elastic IP,
removes the ssh alias. Deliberately keeps the shared SG / key pair / IAM —
and the AgentRQ workspace, which outlives its box.

### Auxiliary AWS resources (opt-in, per run)

Some runs need their agent to provision its own infrastructure — Postgres/RDS,
S3, EC2, DNS — in a separate, pre-existing isolated AWS account, rather than
have it handed to them pre-built. This is opt-in per resource type via four
flags in `placeholders-<slug>.txt`, all default off:

```
PROVISION_POSTGRES=1
PROVISION_S3=1
PROVISION_DNS=1
PROVISION_EC2=1
```

Set `AUX_RESOURCE_PROFILE` to the AWS CLI profile for the isolated account.
`make-new-workspace.sh` calls `provision-aux-aws-resources.sh` automatically
when any flag is set, before launching the instance — it creates a
per-workspace IAM role (`crux-run-$SLUG`) in the main account that can assume
a scoped role (`crux-agent-devops`) in the isolated account, and writes
`AUX_RESOURCE_ACCOUNT_ID`/`AUX_RESOURCE_ROLE_ARN` back into the config file
for the agent's scaffold to read. No other workspace can assume
`crux-agent-devops` — only the one opted-in run's role is trusted.

Run standalone: `./provision-aux-aws-resources.sh [--dry-run] [CONFIG_FILE]`.

`teardown-workspace-aws-resources.sh` calls
`teardown-aux-aws-resources.sh` automatically, which deletes everything found
in the isolated account (it's single-tenant per run) plus both IAM roles. A
slug that never opted in is a no-op.
