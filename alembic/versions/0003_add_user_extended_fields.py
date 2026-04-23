"""Add extended user fields: company, description, webpage, uid/gid, memberOf, photo.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("company", sa.String(), nullable=True))
    op.add_column("user", sa.Column("description", sa.String(), nullable=True))
    op.add_column("user", sa.Column("webpage", sa.String(), nullable=True))
    op.add_column("user", sa.Column("uid_number", sa.Integer(), nullable=True))
    op.add_column("user", sa.Column("gid_number", sa.Integer(), nullable=True))
    op.add_column("user", sa.Column("member_of", sa.JSON(), nullable=True))
    op.add_column("user", sa.Column("photo", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "photo")
    op.drop_column("user", "member_of")
    op.drop_column("user", "gid_number")
    op.drop_column("user", "uid_number")
    op.drop_column("user", "webpage")
    op.drop_column("user", "description")
    op.drop_column("user", "company")
