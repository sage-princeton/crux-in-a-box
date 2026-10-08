"""Fresh PostgreSQL monitoring state. No historical data import."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # This fresh-data cutover deliberately imports no DynamoDB monitoring history.
    op.execute("""
        -- Instance identity survives renames; replacement EC2 instances get new workloads.
        CREATE TABLE workloads (
            key varchar(32) PRIMARY KEY, instance_id text NOT NULL UNIQUE,
            name text NOT NULL, slug text NOT NULL, state text NOT NULL,
            inventory_at bigint NOT NULL, stale_seconds integer NOT NULL
        );
        -- Both assessments share one evidence checkpoint per workload/window.
        -- S3 holds the archives; JSONB holds their references, manifest and model excerpts.
        CREATE TABLE collections (
            workload_key varchar(32) REFERENCES workloads(key) ON DELETE CASCADE,
            window_end bigint NOT NULL, captured_at bigint NOT NULL,
            evidence jsonb NOT NULL, PRIMARY KEY (workload_key, window_end)
        );
        -- Results require a matching collection. 'ingested' records whether an incident
        -- result has been applied, allowing publication retries without new inference.
        CREATE TABLE assessments (
            workload_key varchar(32) NOT NULL, window_end bigint NOT NULL,
            kind text NOT NULL CHECK (kind IN ('status', 'incident')),
            result jsonb NOT NULL, ingested boolean NOT NULL DEFAULT false,
            PRIMARY KEY (workload_key, window_end, kind),
            FOREIGN KEY (workload_key, window_end) REFERENCES collections ON DELETE CASCADE
        );
        -- Detector + source-event anchor keeps the same incident across overlapping
        -- review windows. 'version' rejects operator changes based on stale state.
        CREATE TABLE incidents (
            id uuid PRIMARY KEY, workload_key varchar(32) NOT NULL REFERENCES workloads(key),
            detector text NOT NULL, anchor text NOT NULL,
            status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
            version integer NOT NULL DEFAULT 1, first_seen bigint NOT NULL,
            last_seen bigint NOT NULL, updated_at bigint NOT NULL,
            detail jsonb NOT NULL, UNIQUE (workload_key, detector, anchor)
        );
        -- Supports status filtering and UUID pagination within each workload.
        CREATE INDEX incidents_workload_status ON incidents(workload_key, status, id);
        -- Retrying a window must not append another observation of the same incident.
        CREATE TABLE observations (
            incident_id uuid NOT NULL REFERENCES incidents ON DELETE CASCADE,
            window_end bigint NOT NULL, detail jsonb NOT NULL,
            PRIMARY KEY (incident_id, window_end)
        );
        -- Operator decisions are retained separately from reviewer observations.
        CREATE TABLE incident_events (
            incident_id uuid NOT NULL REFERENCES incidents ON DELETE CASCADE,
            version integer NOT NULL, detail jsonb NOT NULL,
            PRIMARY KEY (incident_id, version)
        );
        -- Tokens are hashes. The app atomically consumes each login request once and
        -- checks expiry before accepting a request or session.
        CREATE TABLE login_requests (
            token text PRIMARY KEY, request_id text NOT NULL, expires_at bigint NOT NULL
        );
        CREATE TABLE sessions (
            token text PRIMARY KEY, actor jsonb NOT NULL, csrf text NOT NULL,
            expires_at bigint NOT NULL
        );
        -- Acknowledged Slack deliveries suppress repeat notices; failed sends can retry.
        CREATE TABLE notices (
            key text PRIMARY KEY, fingerprint text NOT NULL, delivered_at bigint NOT NULL
        );
        -- Atomic, cumulative reservations keep status and incident budgets separate.
        -- Integer microdollars avoid floating-point errors when enforcing spending limits.
        CREATE TABLE budgets (
            scope text PRIMARY KEY, reserved_microusd bigint NOT NULL DEFAULT 0
                CHECK (reserved_microusd >= 0)
        );
    """)


def downgrade():
    # Drop dependent tables first. Rolling back this initial schema discards its data.
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
