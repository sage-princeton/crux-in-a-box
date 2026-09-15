# AgentRQ workspaces

AgentRQ manages Codex and Claude research workspaces on EC2.

- A workspace pairs an AgentRQ workspace with an EC2 instance for one run.
- The controller hosts the dashboard and exchanges messages with workspace
  agents through the Agent Client Protocol (ACP).

## Setup

1. [Create the controller](ec2-control/README.md).
2. [Provision a workspace](ec2-workspaces/README.md) with the required
   platform, model, effort and API key.

Each workspace can use a different agent and configuration.
