"""B2: list_audit_events must not mutate the persisted AuditEvent rows.

The previous implementation overwrote `actor_email` on managed ORM objects
with the resolved display name. With expire_on_commit=False this happens to
not corrupt the DB today (no commit follows), but it's a tripwire — and the
in-memory event has the wrong `actor_email` if something later writes back.
"""

import uuid

from sqlalchemy import select

from not_dot_net.backend.audit import (
    AuditEvent,
    list_audit_events,
    log_audit,
)
from not_dot_net.backend.db import User, session_scope


async def _make_user(email="bob@test.com", full_name="Bob Smith"):
    async with session_scope() as session:
        u = User(id=uuid.uuid4(), email=email, hashed_password="x",
                 full_name=full_name, role="staff")
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u


async def test_list_audit_events_preserves_persisted_actor_email():
    """The DB row's actor_email must remain whatever was logged, even after
    list_audit_events resolves and renders display names."""
    user = await _make_user()
    # Log with a deliberately distinct actor_email so we can check it survives.
    await log_audit(
        "auth", "login",
        actor_id=user.id,
        actor_email="bob@test.com",
    )

    events = await list_audit_events(category="auth")
    assert events, "expected at least one audit event"

    # Re-read from the DB and assert the column was not overwritten.
    async with session_scope() as session:
        rows = (await session.execute(
            select(AuditEvent.actor_email).where(AuditEvent.category == "auth")
        )).all()
    persisted = [r[0] for r in rows]
    assert "bob@test.com" in persisted, (
        f"persisted actor_email got overwritten: {persisted}"
    )
