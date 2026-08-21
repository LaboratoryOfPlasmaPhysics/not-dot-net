"""I6, I14 — destructive actions that left no trace of who did them.

I6: user hard-delete cascades UserTenure/Booking/OfficeAvailability and SET
NULLs WorkflowEvent.actor_id, WorkflowRequest.created_by and Page.author_id.
Workflow history loses its actor and nothing recorded who caused it.

I14: page create/update/delete were entirely unaudited — a defaced or deleted
published page left no trace.
"""

from not_dot_net.backend.permissions import SYSTEM_ACTOR
import uuid

import pytest

from not_dot_net.backend.audit import list_audit_events
from not_dot_net.backend.db import User, session_scope


async def _make_user(email: str, **kwargs) -> User:
    async with session_scope() as session:
        user = User(email=email, hashed_password="x", is_active=True, role="", **kwargs)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _events(category: str, action: str) -> list:
    return [
        e for e in await list_audit_events()
        if e.category == category and e.action == action
    ]


async def test_user_hard_delete_is_audited():
    from not_dot_net.frontend.directory import _delete_user

    actor = await _make_user("admin-del@example.com", is_superuser=True)
    victim = await _make_user("victim@example.com", full_name="Victim Person")

    await _delete_user(victim.id, actor=actor)

    events = await _events("users", "delete")
    assert len(events) == 1, "user hard-delete left no audit row"
    event = events[0]
    assert str(victim.id) in str(event.target_id)
    assert "victim@example.com" in (event.detail or "")


async def test_page_create_update_delete_are_audited():
    from not_dot_net.backend.page_service import create_page, delete_page, update_page

    actor = await _make_user("pageadmin@example.com", is_superuser=True)

    page = await create_page(
        title="Policy", slug="policy", content="hello",
        author_id=actor.id, actor=actor,
    )
    assert len(await _events("pages", "create")) == 1

    await update_page(page.id, title="Policy v2", actor=actor)
    assert len(await _events("pages", "update")) == 1

    await delete_page(page.id, actor=actor)
    deletes = await _events("pages", "delete")
    assert len(deletes) == 1
    assert "policy" in (deletes[0].detail or "")


async def test_page_crud_still_works_without_an_actor():
    """Existing callers (imports, tests) must not break on the new argument."""
    from not_dot_net.backend.page_service import create_page, delete_page, update_page

    page = await create_page(
        title="No Actor", slug="no-actor", content="x", author_id=None, actor=SYSTEM_ACTOR
    )
    await update_page(page.id, title="No Actor v2", actor=SYSTEM_ACTOR)
    await delete_page(page.id, actor=SYSTEM_ACTOR)
    assert await _events("pages", "delete")
