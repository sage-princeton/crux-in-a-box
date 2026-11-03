# Monitoring service

Collect workspace evidence and model traces, assess Run status/incidents, and
serve the authenticated monitoring website. Requires Python 3.12, Node/npm,
Docker and PostgreSQL 18. AWS deployment uses Terraform and an explicit profile.

## Usage

Deploy and verify the service using the [release runbook](ci/README.md#usage).
[design.md](design.md) describes collection, storage, assessments and notifications.

## Development

From the repository root, with a disposable database ending in `_test`:

```sh
python -m pip install -r monitoring/service/web-requirements.txt -r requirements-dev.txt 'moto[s3,ec2,ssm]>=5,<6' PyYAML==6.0.3
export DATABASE_URL='postgresql+psycopg://postgres:password@localhost:5432/monitoring_test'
alembic -c monitoring/service/alembic.ini upgrade head
python -m pytest -q monitoring/service/tests
ruff check monitoring/service
ruff format --check monitoring/service
```

Tests truncate that disposable database. Use the [release checks](ci/README.md#development)
for Terraform and workflow changes.
