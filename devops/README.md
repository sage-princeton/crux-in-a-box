# DevOps

Provision Run Boxes and the independent AgentRQ controller. Requires AWS CLI,
an authenticated explicit AWS profile, Python 3.12, jq, curl and OpenSSH.
Keep credentials and private configuration outside the checkout.

## Usage

1. For ACP, prepare the [controller](ec2-control/README.md).
2. Follow the [Run Box runbook](ec2-workspaces/README.md) for deployment and teardown.
3. Use [AWS utilities](aws/) only for resources managed independently of Runs.

## Development

Use the [repository checks](../README.md#development), then the
[Run Box checks](ec2-workspaces/README.md#development) or
[controller checks](ec2-control/README.md#development) for the code you change.
Exercise provisioning in ciab-devenv against this worktree before deploying to AWS.
The [system design](../README.md#system-design) describes component boundaries.
