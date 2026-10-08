# CRUX monitoring

The monitoring host discovers EC2 instances in the configured account and region,
except instances named `crux-control` or `crux-monitor-worker`. Status and incident
monitoring are always enabled. `fleet.py` contains the FIXME about returning to
`MonitorWithCruxMonitor=1` enrollment.

Every 15 minutes, one collection saves the **whole workspace** and fetches
Langfuse observations. The workspace export streams through SSM into a private,
versioned S3 object. It includes dotfiles, binary files and every directory;
symlinks are preserved without following paths outside the workspace. Temporary
upload credentials permit writing only that snapshot's object. No gateway or
research process is restarted.

The worker verifies the uploaded size and SHA-256, records the complete file
manifest, and derives bounded text excerpts for model context. The archive itself
is never truncated. Collected Langfuse data is also saved before context limits
are applied. Excerpts are scrubbed; the complete private workspace archive keeps
its original contents. Collection failures and missing telemetry remain explicit
coverage gaps.

```mermaid
flowchart LR
  EC2[EC2 inventory] --> Worker[One polling worker]
  Workspace[Whole workspace via SSM] --> S3[Private S3 snapshots]
  Langfuse[Langfuse observations] --> Worker
  S3 --> Worker
  Worker --> Status[Status assessment]
  Worker --> Incidents[Incident assessment]
  Status --> SQL[(RDS PostgreSQL)]
  Incidents --> SQL
  SQL --> Web[Authenticated website]
  Worker --> Slack[Enrollment notices and incident digest]
```

`worker.py` coordinates discovery, saved checkpoints and publication.
`collection.py` collects evidence and builds context; `workspace_copy.py` runs on
the monitored instance. `status_review.py` and `review.py` perform their respective
assessments. `store.py` owns SQL transactions, incident transitions, budgets and
sessions. `database.py` creates connections using RDS-managed credentials and
verified TLS. Alembic owns the schema. Every table and column has a PostgreSQL
comment visible in database tools. PostgreSQL 18 generates UUIDv7 workload and
incident IDs. Unique instance IDs and workload/detector/anchor tuples retain the
same records across discovery and review retries.

A status failure does not prevent the incident assessment. Each assessment has a
separate cumulative inference budget in checked-in `monitoring.json`. Evidence
and assessment checkpoints survive publication failures, so a retry uses saved
artifacts without collecting the run or charging for inference again. A
PostgreSQL advisory lock prevents concurrent worker passes.

“Monitoring has begun” confirms that both assessment attempts are recorded; it
does not promise successful telemetry or inference. Successful Slack deliveries
are acknowledged in SQL; failed deliveries retry. An ambiguous network failure
after Slack accepts a message can cause a duplicate.

A unique EC2 Name maps to the matching Langfuse environment. Missing or duplicate
names do not borrow another run's telemetry. Incident reviewers must come from a
different observed model family; unknown subject models remain a coverage gap.
Stopped instances retain their history and receive no new collections. Instance
IDs keep replacement boxes separate. SQL collections and S3 snapshots have
90-day retention; incidents and operator decisions remain in SQL.

## Development and CI

Use a disposable PostgreSQL database whose name ends in `_test`:

```sh
python3 -m venv /tmp/crux-monitor-venv
/tmp/crux-monitor-venv/bin/pip install -r src/monitoring/web-requirements.txt pytest 'moto[s3,ec2,ssm]>=5,<6' ruff
export DATABASE_URL='postgresql+psycopg://postgres:password@localhost:5432/monitoring_test'
/tmp/crux-monitor-venv/bin/alembic -c src/monitoring/alembic.ini upgrade head
PYTHONPATH=src/monitoring /tmp/crux-monitor-venv/bin/pytest -q src/monitoring/tests
/tmp/crux-monitor-venv/bin/ruff check src/monitoring
/tmp/crux-monitor-venv/bin/ruff format --check src/monitoring
```

Tests truncate their disposable database. CI runs PostgreSQL 18, Alembic upgrade /
downgrade / upgrade, behavioral tests, Terraform validation and mock tests, image
builds, the migration from the built image, and security gates. See
[deployment details](ci/README.md) for the destructive cutover and release checks.

## Cleanup after the first SQL deployment

- Delete the one-time `ci/reset.py` cutover and its deployment step once the old
  schedules, Batch resources, DynamoDB tables and monitoring artifacts are gone.
- Replace the preserved Terraform `web[0]` addresses with uncounted resources
  using `moved` blocks. Keeping them during cutover protects the public address
  and SAML origin.
- Keep one host, one worker and one database until measured load requires more.
  Workspace deduplication could reduce storage later, but must preserve every
  file and a complete recoverable snapshot.
