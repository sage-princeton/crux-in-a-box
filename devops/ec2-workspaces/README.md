# Run Box deployment

Provision a Box and configure its selected Scaffold. Prerequisites are in the
[parent runbook](../README.md); the account must already have the shared network,
instance profile, SSH key and SSM settings described in [operations.md](operations.md).

## Usage

Prepare your Run inputs and set `DROP_IN_PATH` in the private configuration. For a Codex migration run, follow the [CITP pilot guide](../../scaffolds/codex/run-harness/citp-web-pilot/OPERATOR_GUIDE.md). From this directory:

```sh
./make-new-workspace.sh research --base-config /private/base.txt --base-secrets /private/secrets.json --dry-run
./make-new-workspace.sh research --base-config /private/base.txt --base-secrets /private/secrets.json
```

Follow the printed start/observation instructions for the selected Scaffold.
Before teardown, preserve the Run's results, then follow the teardown command in
[operations.md](operations.md#teardown-a-workspace). SDK Run software declarations
are documented in [provisioning.md](provisioning.md).

For an SDK task that must avoid particular paper URLs, the optional
[network proxy](network-proxy.md) loads a TOML blacklist and enforces agent
egress through an HTTPS inspection proxy, which can also block search results
that show the paper. Install it explicitly before launch.

For a workspace that needs auxiliary AWS access, set `ISOLATED_RUN_IDENTITY=1`
in its base config before running `make-new-workspace.sh`. This creates a dedicated
instance role and profile with the shared baseline SSM permission. After provisioning
follow [the auxiliary AWS runbook](../../utilities/aux-aws/README.md).

## Development

After installing the [shared check tools](../../README.md#development), run from
the repository root:

```sh
python -m pytest -q devops/ec2-workspaces/tests
python -m unittest discover -s devops/ec2-workspaces -p 'test_*.py'
shellcheck -x -e SC2029,SC2088 devops/ec2-workspaces/*.sh devops/ec2-workspaces/scaffolds/*.sh
```

Exercise installation, configuration and explicit Run start in ciab-devenv with
your selected source worktree and task inputs.
