# AgentRQ controller

Deploy the controller independently from research Runs. Use the prerequisites and
account setup in the [parent runbook](../README.md). Create `/private/controller.json`
from [controller.example.json](controller.example.json), or use `{}` for a generated
sslip.io hostname. The private env file must define `AGENTRQ_AUTH_ROOT_ACCESS_TOKEN`.

## Usage

From the repository root:

```sh
python devops/controller.py deploy /private/controller.json --profile crux-2-acct-admin-access --secrets /private/controller.env --confirm-idle
python devops/controller.py start /private/controller.json --profile crux-2-acct-admin-access
python devops/controller.py health /private/controller.json --profile crux-2-acct-admin-access
python devops/controller.py destroy /private/controller.json --profile crux-2-acct-admin-access --confirm-idle
```

For a custom hostname, point DNS to the address returned by deploy before start.
Successful start publishes the HTTPS endpoint in SSM for ACP Run discovery.
Teardown retains the controller's address and encrypted data volume. Preserve data
and confirm no active Runs depend on it before teardown. Public HTTPS is authenticated
by AgentRQ; the SSH network is restricted by account setup.

## Development

Install the parent test requirements, then run from the repository root:

```sh
python -m pytest devops/tests/test_controller.py devops/tests/test_fleet.py devops/tests/test_storage.py
shellcheck -x -e SC2029,SC2088 devops/ec2-control/*.sh devops/box/controller-apply.sh
```

Use a disposable environment to verify controller startup and workspace connection.
