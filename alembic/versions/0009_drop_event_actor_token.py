"""Drop the WorkflowEvent.actor_token column.

`save_draft` previously persisted the cleartext target_person token into this
column on every event row, so any admin viewing the workflow event log saw
working impersonation tokens. The column is now unused and dropped.

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("workflow_event")}
    if "actor_token" in cols:
        with op.batch_alter_table("workflow_event") as batch_op:
            batch_op.drop_column("actor_token")


def downgrade() -> None:
    with op.batch_alter_table("workflow_event") as batch_op:
        batch_op.add_column(sa.Column("actor_token", sa.String(255), nullable=True))
