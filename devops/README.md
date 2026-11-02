# DevOps

Prepare an AWS account and deploy isolated SDK, Codex or Claude Runs. Requires
Python 3.12, OpenSSH, an authenticated explicit AWS profile with a region, and an
operator SSH key (`~/.ssh/id_ed25519`, or pass `--ssh-key`). Keep secrets outside source.

## Usage

From the repository root, install tools and set up the account once. Setup is safe
to repeat with the same inputs; deployment discovers its named/tagged AWS resources.

```sh
python3 -m venv /private/crux-tools
/private/crux-tools/bin/pip install ./devops
/private/crux-tools/bin/python devops/setup_account.py --profile crux-2-acct-admin-access \
  --ssh-cidr 203.0.113.10/32 --secrets /private/system-secrets.json
```

Replace the example CIDR with your operator network. Shared secrets use the keys in
[run-system-secrets.json.example](ec2-workspaces/run-system-secrets.json.example).
For ACP, deploy the independent [controller](ec2-control/README.md) first.

For each Run, use [run.example.json](ec2-workspaces/run.example.json). All commands
use that same definition and current AWS/Box state; no local deployment receipt
or generated account file is required.

```sh
/private/crux-tools/bin/python devops/crux.py plan /private/run.json --profile crux-2-acct-admin-access
/private/crux-tools/bin/python devops/crux.py deploy /private/run.json --profile crux-2-acct-admin-access --secrets /private/run-secrets.json
/private/crux-tools/bin/python devops/crux.py start /private/run.json --profile crux-2-acct-admin-access
/private/crux-tools/bin/python devops/crux.py status /private/run.json --profile crux-2-acct-admin-access
# Preserve research results before teardown.
/private/crux-tools/bin/python devops/crux.py destroy /private/run.json --profile crux-2-acct-admin-access
```

`deploy` checks access, creates resources and configures a stopped Run. Retry after
transient failure; an installed Run is neither reconfigured nor restarted. Use a
new Run name for changed settings or a replacement for a terminated Run. `health`
checks installed services without starting them. Logs are under `~/.cache/crux/logs`;
they are diagnostic output and are never read as operational state.

Model and required effort belong only in Run JSON. `inputs` is relative to that
file; SDK Harness TOML omits model/provider values. Optional provider, Box size and
auxiliary resources belong in Run JSON. The secrets JSON maps environment names
to credentials. ACP commands also pass `--secrets` containing `AGENTRQ_ROOT_TOKEN`
for workspace lookup; that root token never reaches a Run Box.

See [design.md](design.md) and [Run software declarations](ec2-workspaces/provisioning.md).

## Development

Install `devops/requirements-test.txt`, then run `python -m pytest devops/tests`
and `python -m ruff check devops` from the repository root. Validate the account
template with the [account checks](account/README.md#development) and exercise
[Box adapters](ec2-workspaces/README.md#development) in ciab-devenv.
