"""Onboarding v2: encrypted_file table, verification code fields, encrypted_file_id FK.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "encrypted_file",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(200), server_default="application/octet-stream"),
        sa.Column("uploaded_by", sa.Uuid(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("retained_until", sa.DateTime(), nullable=True),
        if_not_exists=True,
    )

    with op.batch_alter_table("workflow_request") as batch_op:
        batch_op.add_column(sa.Column("verification_code_hash", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("code_expires_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("code_attempts", sa.Integer(), server_default="0"))

    with op.batch_alter_table("workflow_file") as batch_op:
        batch_op.add_column(
            sa.Column("encrypted_file_id", sa.Uuid(),
                       sa.ForeignKey("encrypted_file.id", ondelete="SET NULL"), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("workflow_file") as batch_op:
        batch_op.drop_column("encrypted_file_id")
    with op.batch_alter_table("workflow_request") as batch_op:
        batch_op.drop_column("code_attempts")
        batch_op.drop_column("code_expires_at")
        batch_op.drop_column("verification_code_hash")
    op.drop_table("encrypted_file")
