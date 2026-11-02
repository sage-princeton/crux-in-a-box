# Invoker library

Python contracts, resource drivers, bundles and Box operations behind the operator
CLI. Requires Python 3.12 and the parent Python project.

Import `RunConfiguration` to validate operator inputs and `Lifecycle` to reconcile
resource drivers. The [DevOps runbook](../README.md) covers operator commands;
[design.md](design.md) describes configuration ownership and reconciliation.

## Development

From the repository root, with the root development dependencies installed:

```sh
python -m pytest devops/tests
python -m ruff check devops
```
