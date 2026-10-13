"""Link a booking to the onboarding request it was made for.

Revision ID: 0023
Revises: 0022
"""
import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


# Batch mode: SQLite cannot ALTER a constraint; on Postgres it emits plain ALTERs.
def upgrade() -> None:
    with op.batch_alter_table("booking") as batch:
        batch.add_column(sa.Column("workflow_request_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_booking_workflow_request_id", "workflow_request",
            ["workflow_request_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_index("ix_booking_workflow_request_id", ["workflow_request_id"])


def downgrade() -> None:
    with op.batch_alter_table("booking") as batch:
        batch.drop_index("ix_booking_workflow_request_id")
        batch.drop_constraint("fk_booking_workflow_request_id", type_="foreignkey")
        batch.drop_column("workflow_request_id")
