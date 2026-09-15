# AgentRQ integration

Local agent setup and tracing configuration. For EC2 deployments, see
[workspace provisioning](../src/ec2-workspaces/README.md).

New EC2 boxes include the repository's `run-harness/` at
`/srv/crux-run/run-harness`, owned by `ubuntu`. It is staged source: its research
placeholders and OpenClaw integrations still need run-specific setup and
adaptation for AgentRQ. The active AgentRQ working directory is `/srv/crux-run`.
