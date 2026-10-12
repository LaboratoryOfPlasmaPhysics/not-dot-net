"""Index audit_event on lower(actor_email), which is what the actor search compares.

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_audit_event_actor_email", table_name="audit_event")
    op.create_index("ix_audit_event_actor_email_lower", "audit_event", [sa.text("lower(actor_email)")])


def downgrade() -> None:
    op.drop_index("ix_audit_event_actor_email_lower", table_name="audit_event")
    op.create_index("ix_audit_event_actor_email", "audit_event", ["actor_email"])
