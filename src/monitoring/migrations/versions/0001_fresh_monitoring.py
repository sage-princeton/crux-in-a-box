"""Fresh PostgreSQL monitoring state. No historical data import."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # This fresh-data cutover deliberately imports no DynamoDB monitoring history.
    op.execute("""
        CREATE TABLE workloads (
            key uuid PRIMARY KEY DEFAULT uuidv7(), instance_id text NOT NULL UNIQUE,
            name text NOT NULL, slug text NOT NULL, state text NOT NULL,
            inventory_at bigint NOT NULL, stale_seconds integer NOT NULL
        );
        COMMENT ON TABLE workloads IS
            'Discovered EC2 instances; instance identity survives renames and separates replacement boxes.';
        COMMENT ON COLUMN workloads.key IS
            'Database-generated UUIDv7 workload identity, retained across inventory updates.';
        COMMENT ON COLUMN workloads.instance_id IS
            'Unique EC2 instance ID used to match repeated inventory discoveries.';
        COMMENT ON COLUMN workloads.name IS 'Current EC2 Name tag, or instance ID when unnamed.';
        COMMENT ON COLUMN workloads.slug IS 'Display label derived from the name and disambiguated for duplicates.';
        COMMENT ON COLUMN workloads.state IS 'Last observed EC2 state, or no longer enrolled when absent from inventory.';
        COMMENT ON COLUMN workloads.inventory_at IS 'Inventory refresh time in Unix seconds.';
        COMMENT ON COLUMN workloads.stale_seconds IS 'Seconds after which inventory or evidence is considered stale.';

        CREATE TABLE collections (
            workload_key uuid REFERENCES workloads(key) ON DELETE CASCADE,
            window_end bigint NOT NULL, captured_at bigint NOT NULL,
            evidence jsonb NOT NULL, PRIMARY KEY (workload_key, window_end)
        );
        COMMENT ON TABLE collections IS
            'One saved evidence checkpoint per workload and time window, shared by status and incident assessments.';
        COMMENT ON COLUMN collections.workload_key IS 'Workload UUID; deleting the workload removes its collections.';
        COMMENT ON COLUMN collections.window_end IS 'End of the collection window in Unix seconds; part of the checkpoint identity.';
        COMMENT ON COLUMN collections.captured_at IS 'Time the collection checkpoint was first saved, in Unix seconds.';
        COMMENT ON COLUMN collections.evidence IS
            'Shared evidence with S3 archive references, full workspace manifest, model excerpts, telemetry and coverage gaps.';

        CREATE TABLE assessments (
            workload_key uuid NOT NULL, window_end bigint NOT NULL,
            kind text NOT NULL CHECK (kind IN ('status', 'incident')),
            result jsonb NOT NULL, ingested boolean NOT NULL DEFAULT false,
            PRIMARY KEY (workload_key, window_end, kind),
            FOREIGN KEY (workload_key, window_end) REFERENCES collections ON DELETE CASCADE
        );
        COMMENT ON TABLE assessments IS
            'Saved status and incident attempts backed by a collection checkpoint; retries reuse the saved result.';
        COMMENT ON COLUMN assessments.workload_key IS 'Workload UUID in the shared collection identity.';
        COMMENT ON COLUMN assessments.window_end IS 'Collection window end in Unix seconds; identifies the reviewed evidence.';
        COMMENT ON COLUMN assessments.kind IS 'Assessment path: status or incident, with separate results and budgets.';
        COMMENT ON COLUMN assessments.result IS 'Saved assessment outcome, report, model provenance and private artifact references.';
        COMMENT ON COLUMN assessments.ingested IS
            'Whether an incident result has been applied to incidents and observations; false permits publication retry.';

        CREATE TABLE incidents (
            id uuid PRIMARY KEY DEFAULT uuidv7(), workload_key uuid NOT NULL REFERENCES workloads(key),
            detector text NOT NULL, anchor text NOT NULL,
            status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
            version integer NOT NULL DEFAULT 1, first_seen bigint NOT NULL,
            last_seen bigint NOT NULL, updated_at bigint NOT NULL,
            detail jsonb NOT NULL, UNIQUE (workload_key, detector, anchor)
        );
        COMMENT ON TABLE incidents IS
            'Durable incidents deduplicated by workload, detector and source-event anchor across review windows.';
        COMMENT ON COLUMN incidents.id IS 'Database-generated UUIDv7 identity, retained for every observation and operator action.';
        COMMENT ON COLUMN incidents.workload_key IS 'Owning workload UUID; retained incidents prevent deletion of their workload.';
        COMMENT ON COLUMN incidents.detector IS 'Validated finding detector or monitoring health detector.';
        COMMENT ON COLUMN incidents.anchor IS 'Stable source-event anchor; monitoring health incidents use the workload anchor.';
        COMMENT ON COLUMN incidents.status IS 'Operator-controlled open or closed state; repeated observations do not reopen it.';
        COMMENT ON COLUMN incidents.version IS 'Optimistic concurrency version incremented by new observations and operator changes.';
        COMMENT ON COLUMN incidents.first_seen IS 'Earliest observed review window end in Unix seconds.';
        COMMENT ON COLUMN incidents.last_seen IS 'Latest observed review window end in Unix seconds.';
        COMMENT ON COLUMN incidents.updated_at IS 'Time of the latest new observation or operator change, in Unix seconds.';
        COMMENT ON COLUMN incidents.detail IS 'Current title, summary, kind, severity and confidence from the latest observation.';
        CREATE INDEX incidents_workload_status ON incidents(workload_key, status, id);
        COMMENT ON INDEX incidents_workload_status IS 'Supports workload/status filtering and UUID cursor pagination.';

        CREATE TABLE observations (
            incident_id uuid NOT NULL REFERENCES incidents ON DELETE CASCADE,
            window_end bigint NOT NULL, detail jsonb NOT NULL,
            PRIMARY KEY (incident_id, window_end)
        );
        COMMENT ON TABLE observations IS 'Reviewer observations; one per incident and review window makes repeated ingestion idempotent.';
        COMMENT ON COLUMN observations.incident_id IS 'Incident UUID; deleting the incident removes its observations.';
        COMMENT ON COLUMN observations.window_end IS 'Observed review window end in Unix seconds; part of the deduplication key.';
        COMMENT ON COLUMN observations.detail IS 'Private finding, cited evidence, review identity, artifact references and model provenance.';

        CREATE TABLE incident_events (
            incident_id uuid NOT NULL REFERENCES incidents ON DELETE CASCADE,
            version integer NOT NULL, detail jsonb NOT NULL,
            PRIMARY KEY (incident_id, version)
        );
        COMMENT ON TABLE incident_events IS 'Operator decision history, retained separately from reviewer observations.';
        COMMENT ON COLUMN incident_events.incident_id IS 'Incident UUID; deleting the incident removes its operator history.';
        COMMENT ON COLUMN incident_events.version IS 'Incident version after the operator action; orders and identifies the event.';
        COMMENT ON COLUMN incident_events.detail IS 'Action time, old and new status, authenticated actor, note and resulting version.';

        CREATE TABLE login_requests (
            token text PRIMARY KEY, request_id text NOT NULL, expires_at bigint NOT NULL
        );
        COMMENT ON TABLE login_requests IS 'Short-lived SAML browser bindings consumed atomically once during successful sign-in.';
        COMMENT ON COLUMN login_requests.token IS 'SHA-256 digest of the browser nonce; the raw nonce is never stored.';
        COMMENT ON COLUMN login_requests.request_id IS 'SAML AuthnRequest ID matched against the response InResponseTo value.';
        COMMENT ON COLUMN login_requests.expires_at IS 'Sign-in deadline in Unix seconds; expired bindings cannot be consumed.';

        CREATE TABLE sessions (
            token text PRIMARY KEY, actor jsonb NOT NULL, csrf text NOT NULL,
            expires_at bigint NOT NULL
        );
        COMMENT ON TABLE sessions IS 'Server-side authenticated sessions with explicit expiry and CSRF protection.';
        COMMENT ON COLUMN sessions.token IS 'SHA-256 digest of the secure session cookie; the raw cookie token is never stored.';
        COMMENT ON COLUMN sessions.actor IS 'Authenticated SAML identity containing the subject ID and identity-provider issuer.';
        COMMENT ON COLUMN sessions.csrf IS 'Random CSRF token compared against authenticated operator form submissions.';
        COMMENT ON COLUMN sessions.expires_at IS 'Session expiry in Unix seconds, bounded by the SAML session deadline.';

        CREATE TABLE notices (
            key text PRIMARY KEY, fingerprint text NOT NULL, delivered_at bigint NOT NULL
        );
        COMMENT ON TABLE notices IS 'Acknowledged Slack deliveries suppress repeat notices; failed sends remain retryable.';
        COMMENT ON COLUMN notices.key IS 'Logical notification identity, such as workload enrollment or an incident digest.';
        COMMENT ON COLUMN notices.fingerprint IS 'Digest of the last acknowledged notification content or logical state.';
        COMMENT ON COLUMN notices.delivered_at IS 'Time Slack acknowledged the last successful delivery, in Unix seconds.';

        CREATE TABLE budgets (
            scope text PRIMARY KEY, reserved_microusd bigint NOT NULL DEFAULT 0
                CHECK (reserved_microusd >= 0)
        );
        COMMENT ON TABLE budgets IS 'Atomic cumulative inference reservations; status and incident spending use separate scopes.';
        COMMENT ON COLUMN budgets.scope IS 'Assessment budget identity: status or incident.';
        COMMENT ON COLUMN budgets.reserved_microusd IS 'Cumulative reserved US dollars times one million; integers avoid floating-point errors.';

        COMMENT ON TABLE alembic_version IS 'Alembic schema revision bookkeeping; updated by migrations.';
        COMMENT ON COLUMN alembic_version.version_num IS 'Currently applied Alembic revision identifier.';
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
