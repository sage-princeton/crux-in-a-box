# Shared AWS installation

CloudFormation template consumed by `setup_account.py` and discovered by the
Invoker through the named/tagged stack. Account setup commands and prerequisites
are in the [parent runbook](../README.md); design is in [design.md](../design.md).
Identical setup reuses a ready installation, including one with a completed
reviewed update or rollback; changed settings require a CloudFormation change set.

## Development

Validate from the repository root after installing `devops/requirements-test.txt`:

```sh
cfn-lint devops/account/template.yaml
```
