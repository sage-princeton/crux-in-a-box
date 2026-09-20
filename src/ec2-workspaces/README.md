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

By default each workspace gets its own Elastic IP, allocated and tagged to its slug.
Pass `--elastic-ip <allocation-id>` to reuse an existing one instead — e.g. an address
a web host has allowlisted for crawling, that needs to stay stable across pilots and
workspaces. An EIP can only be associated with one running instance at a time, so only
one workspace can hold it live at once; provisioning fails fast if it's already in use
by another live workspace. Tear that workspace down (or use a different address) first.
Use `../../utils/manage-elastic-ips.sh` to allocate, list and release the standalone
addresses these overrides point at, independent of any one workspace.

### Teardown a workspace

This will:

- Delete the AWS assets
- **Keep** the workspace in Agent RQ

**`teardown-workspace-aws-resources.sh`** — laptop. Terminates one box, releases its Elastic IP,
removes the ssh alias. Deliberately keeps the shared SG / key pair / IAM —
and the AgentRQ workspace, which outlives its box. If the box used an `--elastic-ip`
override, that address is left allocated (just disassociated) so the next workspace
can reuse it — pass it as `--elastic-ip` again.
