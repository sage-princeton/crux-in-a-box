# CRUX in a box

Provision a Box, configure a Scaffold, and start an autonomous research Run.
The supported Scaffolds are AgentRQ/ACP with Codex or Claude, and the custom
Agents SDK Scaffold. The legacy OpenClaw implementation has been removed; Git history retains it.

## Concepts

| Term | Meaning |
| --- | --- |
| Box | The remote execution environment (EC2 in production, isolated Ubuntu locally). |
| Run | One CRUX task executed on a Box. |
| Scaffold | The execution environment, agent orchestration and operator-message interface. |
| Invoker | The environment from which a Run is deployed and started, usually a developer's laptop. |
| Agent | The outer-loop agent or any delegate participating in a Run. |
| Harness | The assembled Scaffold, Agents, context and configuration on a Box for a Run. |

An AgentRQ workspace is a controller object. A workspace directory is the
Agent's working directory. Neither is the Box's lifecycle identity.

## Start a Run

1. Read [DevOps](devops/README.md) and prepare private configuration and credentials.
2. Validate the inputs on the Invoker before creating resources.
3. Create the Box and its declared auxiliary resources.
4. Assemble and transfer the configuration bundle, configure the Scaffold and verify health.
5. Start the Run explicitly through the selected Scaffold.

Current command examples and prerequisites are in the
[Box runbook](devops/ec2-workspaces/README.md). AgentRQ also requires a
[controller](devops/ec2-control/README.md). The
[SDK guide](scaffolds/sdk/README.md) explains its Run inputs and start command.
For local execution use the paired `ciab-devenv` checkout: select this worktree,
reprovision, inspect artifacts, then export/cite evidence and reset the named environment.

## Components

| Path | Responsibility |
| --- | --- |
| `devops/` | Box/resource lifecycle, configuration bundles and deployment contracts. |
| `monitoring/service/` | Monitoring application, tests and deployment configuration. |
| `monitoring/tracing/` | Codex/Claude tracing integrations and their tests. |
| `monitoring/costs/` | Cross-scaffold Langfuse cost collector and deployment. |
| `scaffolds/{codex,claude}/` | CLI configuration and scaffold-specific Run inputs. |
| `scaffolds/sdk/` | SDK engine, tests and `run-harness/` input bundles. |
| `utilities/` | Secret scanning and reusable bootstrap helpers. |

## Checks

Install `requirements-dev.txt` and the selected components' dependencies.
Run `python -m ruff check .` and `python -m pytest -q` from the repository root.
Monitoring tests require disposable PostgreSQL; see its component runbook.
Scan changed files with `python utilities/security/scan-secrets.py PATH...`.
The [migration map](devops/README.md#layout-migration) covers older branches.
