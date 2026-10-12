"""The audit actor search can use an index (review 2026-10-12 F14).

The search compares lower(actor_email), but the index was on the bare
column, so every search scanned the whole audit table.
"""

from sqlalchemy import select, text
from sqlalchemy.dialects import sqlite

from not_dot_net.backend.audit import AuditEvent, actor_email_clause
from not_dot_net.backend.db import session_scope


async def test_actor_search_uses_an_index():
    query = select(AuditEvent.id).where(actor_email_clause("Someone@LPP.example"))
    sql = str(query.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    async with session_scope() as session:
        plan = (await session.execute(text(f"EXPLAIN QUERY PLAN {sql}"))).all()
    assert any("USING INDEX" in str(row[-1]) or "USING COVERING INDEX" in str(row[-1]) for row in plan), plan
