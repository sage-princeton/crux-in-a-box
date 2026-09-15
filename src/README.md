# Agent RQ - crus-in-a-box v3

## Terminology

- **Workspace**: This is a space for a single run. This corresponds to a
  workspace in the Agent RQ interface and a EC2 box.
- **Controller**: This is the Agent RQ interface. It's where we send and
  receive messages to/from agents. Workspaces appear on the controller's
  dashboard. We can connect to the controller's dashboard via https.

## How it works

- The interface between the controller and each workspace's box is ACP
- We abstract out multiple agents; each EC2 instance can have it's own agent

## Usage

### Adding a new workspace

Use `make-new-workspace.sh`. This will set up a workspace on the controller
and will also provision all required AWS resources.
Set `AGENT_PLATFORM=codex|claude` and the matching model, effort, and API-key
settings; see [workspace provisioning](ec2-workspaces/README.md).
FIXME: consider copying in the run-harness directory here

_(more usage information to be added here)_
