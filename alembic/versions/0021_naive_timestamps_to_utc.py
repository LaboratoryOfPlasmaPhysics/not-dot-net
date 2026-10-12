"""Convert server_default=now() timestamps from the server timezone to UTC.

Timestamps are naive UTC throughout the app, but Postgres fills now() in the
session timezone, which defaulted to the server's (Europe/Paris in
production). Python-written columns were UTC, so the two clocks were mixed.
From now on db.engine_options pins every app session to UTC; this converts
the rows written before.

The source zone is read from the server (this migration's own session is not
pinned), so a server already on UTC makes this a no-op. SQLite is UTC already.

Revision ID: 0021
Revises: 0020
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

# Every naive timestamp column filled by server_default/onupdate now().
# uid_allocation.acquired_at is timestamptz and already absolute.
NOW_FILLED_COLUMNS = [
    ("audit_event", "created_at"),
    ("booking", "created_at"),
    ("encrypted_file", "uploaded_at"),
    ("floor_plan", "created_at"),
    ("mail_outbox", "created_at"),
    ("map_point", "created_at"),
    ("office_availability", "created_at"),
    ("page", "created_at"),
    ("page", "updated_at"),
    ("resource", "created_at"),
    ("user_tenure", "created_at"),
    ("workflow_event", "created_at"),
    ("workflow_failed_effect", "created_at"),
    ("workflow_file", "uploaded_at"),
    ("workflow_request", "created_at"),
    ("workflow_request", "updated_at"),
]


def _shift(from_zone_sql: str, to_zone_sql: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, column in NOW_FILLED_COLUMNS:
        op.execute(
            f'UPDATE "{table}" SET "{column}" = '
            f'("{column}" AT TIME ZONE {from_zone_sql}) AT TIME ZONE {to_zone_sql} '
            f'WHERE "{column}" IS NOT NULL'
        )


def upgrade() -> None:
    _shift("current_setting('TimeZone')", "'UTC'")


def downgrade() -> None:
    _shift("'UTC'", "current_setting('TimeZone')")
