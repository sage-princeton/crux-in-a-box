# Run Box software

Box-side installers and Scaffold adapters used by the Invoker. These run as root
on Ubuntu; invoke deployment through the [parent runbook](../README.md).

To declare Run-specific packages and commands, add `provision.json` to the Run's
input directory using [provisioning.md](provisioning.md). The selected Scaffold's
install/configure hooks leave its systemd service stopped until explicit start.

## Development

After installing the [shared check tools](../../README.md#development), run from
the repository root:

```sh
python -m pytest -q devops/ec2-workspaces/tests
python -m unittest discover -s devops/ec2-workspaces -p 'test_*.py'
shellcheck -x -e SC2029,SC2088 devops/ec2-workspaces/*.sh devops/ec2-workspaces/scaffolds/*.sh
```

Exercise installation, configuration and explicit Run start in ciab-devenv with
your selected source worktree and caller-owned task inputs. Codex and Claude
`run-harness/` directories contain no example inputs.
