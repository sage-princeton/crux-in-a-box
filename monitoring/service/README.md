# Monitoring service

Collect workspace evidence and model traces, assess Run status/incidents, and
serve the authenticated monitoring website. Requires Python 3.12, Node/npm,
Docker and PostgreSQL 18. AWS deployment uses Terraform and an explicit profile.

## Usage

Deploy and verify the service using the [release runbook](ci/README.md#usage).
[design.md](design.md) describes collection, storage, assessments and notifications.

## Development

From the repository root:

```sh
python -m pip install -r requirements-dev.txt
ruff check monitoring/service
ruff format --check monitoring/service
```

Use the [release checks](ci/README.md#development) for Terraform and workflow
changes. CI builds and scans the app and proxy images; the release workflow
checks HTTPS readiness and authentication after deployment.
