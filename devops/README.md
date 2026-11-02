# DevOps

Shared AWS installation and native resource drivers. Requires Python 3.12, OpenSSH,
an authenticated explicit AWS profile with a region, and an operator SSH key.
Keep credentials outside source.

## Usage

From the repository root:

```sh
python3 -m venv /private/crux-tools
/private/crux-tools/bin/pip install ./devops
/private/crux-tools/bin/python devops/setup_account.py --profile crux-2-acct-admin-access \
  --ssh-cidr 203.0.113.10/32 --secrets /private/system-secrets.json
```

Replace the example CIDR with your operator network. The default key is
`~/.ssh/id_ed25519`; pass `--ssh-key` for another key. Shared secrets follow
[run-system-secrets.json.example](ec2-workspaces/run-system-secrets.json.example).
Identical setup can be repeated from any authorized laptop. Existing settings
changes require a reviewed CloudFormation change set.

The script creates the named/tagged `crux-account` stack and shared SSM parameter.
Drivers discover cloud resources at runtime; setup produces no local state file.
[design.md](design.md) describes account installation and resource reconciliation.


## Development

Install `devops/requirements-test.txt`, then run `python -m pytest devops/tests`
and `python -m ruff check devops` from the repository root. Validate the account
template with the [account checks](account/README.md#development). Exercise the
selected worktree in ciab-devenv before deploying to AWS.
