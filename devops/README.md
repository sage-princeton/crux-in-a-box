# DevOps

Provision Run Boxes and the independent AgentRQ controller. Requires AWS CLI,
an authenticated explicit AWS profile, Python 3.12, jq, curl and OpenSSH.
Keep credentials and private configuration outside the checkout.

1. For ACP, prepare the [controller](ec2-control/README.md).
2. Follow the [Run Box runbook](ec2-workspaces/README.md) for deployment and teardown.
3. Use [AWS utilities](aws/) only for resources managed independently of Runs.

The [system design](../design.md) defines shared terminology and component boundaries.
