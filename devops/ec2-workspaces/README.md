# Run Box deployment

Provision a Box and configure its selected Scaffold. Prerequisites are in the
[parent runbook](../README.md); the account must already have the shared network,
instance profile, SSH key and SSM settings described in [operations.md](operations.md).

From this directory, using prepared private config/secrets files:

```sh
./make-new-workspace.sh research --base-config /private/base.txt --base-secrets /private/secrets.json --dry-run
./make-new-workspace.sh research --base-config /private/base.txt --base-secrets /private/secrets.json
```

Follow the printed start/observation instructions for the selected Scaffold.
Before teardown, preserve the Run's results, then follow the teardown command in
[operations.md](operations.md#teardown-a-workspace). SDK Run software declarations
are documented in [provisioning.md](provisioning.md).
