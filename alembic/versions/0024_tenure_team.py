"""Record the team on each tenure, so headcounts per team work at any date.

Only open tenures get today's team: for a closed one it is unknown, and
copying today's value would invent history. The archive import fills those.

Revision ID: 0024
Revises: 0023
"""
import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_tenure") as batch:
        batch.add_column(sa.Column("team", sa.String(200), nullable=True))
    op.execute(
        'UPDATE user_tenure SET team = (SELECT team FROM "user" WHERE "user".id = user_tenure.user_id) '
        "WHERE end_date IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("user_tenure") as batch:
        batch.drop_column("team")
