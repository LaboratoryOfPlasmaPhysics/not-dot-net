"""Add workflow_failed_effect — durable queue of step effects that failed.

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_failed_effect",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("step_key", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["request_id"], ["workflow_request.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_failed_effect_request_id", "workflow_failed_effect", ["request_id"]
    )
    op.create_index(
        "ix_workflow_failed_effect_created_at", "workflow_failed_effect", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_failed_effect_created_at", table_name="workflow_failed_effect")
    op.drop_index("ix_workflow_failed_effect_request_id", table_name="workflow_failed_effect")
    op.drop_table("workflow_failed_effect")
