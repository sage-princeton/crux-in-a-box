# Deployment contracts

Run configuration and resource lifecycle interfaces used by CRUX Invokers. Requires
Python 3.12 and the repository's test dependencies.

Import `RunConfiguration` to validate operator inputs and `Lifecycle` to reconcile
resource drivers. The [DevOps runbook](../README.md) covers operator commands;
[design.md](design.md) describes configuration ownership and reconciliation.

## Development

From the repository root, with the root development dependencies installed:

```sh
python -m pytest devops/tests
python -m ruff check devops
```
