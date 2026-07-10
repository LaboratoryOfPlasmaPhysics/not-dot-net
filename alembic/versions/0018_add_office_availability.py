"""Add owner_user_id to resource and the office_availability table.

Revision ID: 0018
Revises: 0017
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resource", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_resource_owner_user_id", "resource", "user",
        ["owner_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_table(
        "office_availability",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("offered_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["resource_id"], ["resource.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["offered_by"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_office_availability_resource_id", "office_availability", ["resource_id"])


def downgrade() -> None:
    op.drop_index("ix_office_availability_resource_id", table_name="office_availability")
    op.drop_table("office_availability")
    op.drop_constraint("fk_resource_owner_user_id", "resource", type_="foreignkey")
    op.drop_column("resource", "owner_user_id")
