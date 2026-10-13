"""Announce newly created incidents without replaying existing history."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE workloads ADD COLUMN slack_incident_cursor uuid NOT NULL
            DEFAULT '00000000-0000-0000-0000-000000000000';
        COMMENT ON COLUMN workloads.slack_incident_cursor IS
            'Latest incident UUID acknowledged by Slack, or the nil UUID before delivery; the migration skips existing history.';
        UPDATE workloads w SET slack_incident_cursor=COALESCE((
            SELECT i.id FROM incidents i WHERE i.workload_key=w.key ORDER BY i.id DESC LIMIT 1
        ), slack_incident_cursor);
    """)


def downgrade():
    op.execute("ALTER TABLE workloads DROP COLUMN slack_incident_cursor")
