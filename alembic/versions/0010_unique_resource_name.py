"""Add UNIQUE constraint on resource.name. Deduplicate first.

Existing duplicates (if any) get a `(2)`/`(3)`/... suffix appended so the
constraint can be applied without dropping data. Choosing the surviving
"(1)" by oldest created_at to keep audit trails coherent.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Find duplicate names (everything except the oldest row per name).
    rows = bind.execute(sa.text("""
        SELECT id, name FROM resource
        WHERE id NOT IN (
            SELECT id FROM (
                SELECT id, name,
                       ROW_NUMBER() OVER (PARTITION BY name ORDER BY created_at, id) AS rn
                FROM resource
            ) t WHERE rn = 1
        )
    """)).fetchall()

    # Suffix duplicates with " (2)", " (3)", … per base name.
    seen: dict[str, int] = {}
    for row in rows:
        rid, name = row
        seen[name] = seen.get(name, 1) + 1
        new_name = f"{name} ({seen[name]})"
        bind.execute(
            sa.text("UPDATE resource SET name = :n WHERE id = :i"),
            {"n": new_name, "i": rid},
        )

    with op.batch_alter_table("resource") as batch_op:
        batch_op.create_unique_constraint("uq_resource_name", ["name"])


def downgrade() -> None:
    with op.batch_alter_table("resource") as batch_op:
        batch_op.drop_constraint("uq_resource_name", type_="unique")
