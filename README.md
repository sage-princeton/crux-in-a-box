# CRUX in a box

Deploy autonomous agents to EC2, run tasks, and inspect their work. Supported
Scaffolds are Agents SDK and AgentRQ/ACP with Codex or Claude.

> [!NOTE]
> This code is set up specifically to support [CRUX](https://cruxevals.com/) evaluations.
> We strongly encourage you to remix, adapt, and reuse this scaffold to conduct
> your own CRUX-style experiments, and to meet your specific research goals.

## Prerequisites

AWS deployment requires an authenticated explicit AWS profile and the tools
listed in the [DevOps runbook](devops/README.md). Provider credentials and private
Run configuration stay outside Git. Development uses Python 3.12 and the selected
component's dependencies.

## Getting started

1. Follow [DevOps](devops/README.md) to prepare and deploy a Run.
2. Use the selected [Scaffold](scaffolds/README.md) to begin and observe it.
3. Preserve the results before following the teardown instructions.

## Development

Install the dependencies in each component's Development section before running
its tests. From the repository root, shared checks are:

```sh
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest -q
```

The full suite needs all component dependencies and disposable PostgreSQL; see
[monitoring](monitoring/service/README.md#development),
[SDK](scaffolds/sdk/README.md#development) and
[tracing](monitoring/tracing/tests/README.md#development). CI scans complete PR
commit ranges with TruffleHog.

## Layout

| Directory | Purpose |
| --- | --- |
| [devops/](devops/README.md) | Box provisioning, Scaffold configuration and controller deployment. |
| [scaffolds/](scaffolds/README.md) | Agent orchestration, prompts, tools, context and Run inputs. |
| [monitoring/](monitoring/README.md) | Fleet monitoring, traces and model-cost collection. |
| [utilities/](utilities/README.md) | Support tools used independently of deployment. |
| [src/](src/README.md) | Temporary Terraform path compatibility. |

[design.md](design.md) explains how these components work together.

## Terminology

| Term | Meaning |
| --- | --- |
| Box | Remote execution environment, normally EC2. |
| Run | Execution of a task on a Box. |
| Scaffold | Defines and orchestrates one or more Agents, providing tools, context, prompts and steering interfaces. |
| Invoker | Environment from which deployment and Run start are invoked. |
| Agent | The outer-loop agent or one of its delegates. |
| Harness | Scaffold, Agents, context and configuration assembled for a Run. |
