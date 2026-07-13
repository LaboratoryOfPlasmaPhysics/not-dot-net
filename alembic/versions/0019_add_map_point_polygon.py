"""Add MapPoint.polygon (JSON list of [x,y] vertices) for zone/room outlines.

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "map_point",
        sa.Column("polygon", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("map_point", "polygon")
