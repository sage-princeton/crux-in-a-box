"""Fresh PostgreSQL monitoring state. No historical data import."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE workloads (
            key varchar(32) PRIMARY KEY, instance_id text NOT NULL UNIQUE,
            name text NOT NULL, slug text NOT NULL, state text NOT NULL,
            inventory_at bigint NOT NULL, stale_seconds integer NOT NULL
        );
        CREATE TABLE collections (
            workload_key varchar(32) REFERENCES workloads(key) ON DELETE CASCADE,
            window_end bigint NOT NULL, captured_at bigint NOT NULL,
            evidence jsonb NOT NULL, PRIMARY KEY (workload_key, window_end)
        );
        CREATE TABLE assessments (
            workload_key varchar(32) NOT NULL, window_end bigint NOT NULL,
            kind text NOT NULL CHECK (kind IN ('status', 'incident')),
            result jsonb NOT NULL, ingested boolean NOT NULL DEFAULT false,
            PRIMARY KEY (workload_key, window_end, kind),
            FOREIGN KEY (workload_key, window_end) REFERENCES collections ON DELETE CASCADE
        );
        CREATE TABLE incidents (
            id uuid PRIMARY KEY, workload_key varchar(32) NOT NULL REFERENCES workloads(key),
            detector text NOT NULL, anchor text NOT NULL,
            status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
            version integer NOT NULL DEFAULT 1, first_seen bigint NOT NULL,
            last_seen bigint NOT NULL, updated_at bigint NOT NULL,
            detail jsonb NOT NULL, UNIQUE (workload_key, detector, anchor)
        );
        CREATE INDEX incidents_workload_status ON incidents(workload_key, status, id);
        CREATE TABLE observations (
            incident_id uuid NOT NULL REFERENCES incidents ON DELETE CASCADE,
            window_end bigint NOT NULL, detail jsonb NOT NULL,
            PRIMARY KEY (incident_id, window_end)
        );
        CREATE TABLE incident_events (
            incident_id uuid NOT NULL REFERENCES incidents ON DELETE CASCADE,
            version integer NOT NULL, detail jsonb NOT NULL,
            PRIMARY KEY (incident_id, version)
        );
        CREATE TABLE login_requests (
            token text PRIMARY KEY, request_id text NOT NULL, expires_at bigint NOT NULL
        );
        CREATE TABLE sessions (
            token text PRIMARY KEY, actor jsonb NOT NULL, csrf text NOT NULL,
            expires_at bigint NOT NULL
        );
        CREATE TABLE notices (
            key text PRIMARY KEY, fingerprint text NOT NULL, delivered_at bigint NOT NULL
        );
        CREATE TABLE budgets (
            scope text PRIMARY KEY, reserved_microusd bigint NOT NULL DEFAULT 0
                CHECK (reserved_microusd >= 0)
        );
    """)


def downgrade():
    for table in (
        "budgets",
        "notices",
        "sessions",
        "login_requests",
        "incident_events",
        "observations",
        "incidents",
        "assessments",
        "collections",
        "workloads",
    ):
        op.drop_table(table)
