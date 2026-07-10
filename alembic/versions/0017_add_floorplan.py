"""Add floor_plan and map_point tables.

Revision ID: 0017
Revises: 0016
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "floor_plan",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("image_path", sa.String(length=500), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=False),
        sa.Column("height_px", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "map_point",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("floor_plan_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("x", sa.Integer(), nullable=False),
        sa.Column("y", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["floor_plan_id"], ["floor_plan.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["resource.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_map_point_floor_plan_id", "map_point", ["floor_plan_id"])


def downgrade() -> None:
    op.drop_index("ix_map_point_floor_plan_id", table_name="map_point")
    op.drop_table("map_point")
    op.drop_table("floor_plan")
