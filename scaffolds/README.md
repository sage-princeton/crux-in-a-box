# Scaffolds

Scaffolds define and orchestrate one or more Agents to carry out a task, providing
tools, context, prompts and steering interfaces. This directory contains SDK code,
Codex/Claude configuration and Run inputs.

## Usage

For SDK prerequisites and local commands, see [sdk](sdk/README.md). To deploy any
Scaffold to a Box, use the [DevOps runbook](../devops/README.md). Run-specific
instructions live beside the selected inputs. Codex and Claude `run-harness/`
directories are empty placeholders; supply your own task inputs.

## Development

Use the [SDK checks](sdk/README.md#development) for orchestration changes and the
[monitoring checks](../monitoring/README.md#development) for Codex exporters.
Exercise configuration changes with the selected Scaffold in ciab-devenv.
