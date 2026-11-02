# CRUX in a box

Deploy autonomous research agents to EC2 and inspect their work. Supported
Scaffolds are Agents SDK and AgentRQ/ACP with Codex or Claude.

AWS deployment requires an authenticated explicit AWS profile and the tools
listed in the [DevOps runbook](devops/README.md). Provider credentials and private
Run configuration stay outside Git.

1. Follow [DevOps](devops/README.md) to prepare and deploy a Run.
2. Use the selected [Scaffold](scaffolds/README.md) to begin and observe it.
3. Preserve the results before following the teardown instructions.

For development, use Python 3.12 and install the selected component's requirements:

```sh
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest -q
```

Monitoring tests also require disposable PostgreSQL; see its
[runbook](monitoring/service/README.md). CI scans complete PR commit ranges with
TruffleHog. [design.md](design.md) explains component boundaries and terminology.
