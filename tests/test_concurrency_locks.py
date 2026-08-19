"""I5 — check-then-act window with no row lock.

I4 (tenure overlap) is deliberately NOT locked — see the comment in
tenure_service.add_tenure: it runs inside submit_step's open session, so a
nested lock deadlocks. The overlap validation itself is still pinned below.

SQLite serialises writers, so a genuine interleaving cannot be provoked here.
These follow the project's existing convention (test_booking_service_fixes.py):
assert the lock is actually taken, since that is what closes the window on
PostgreSQL where the interleaving is real.
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from not_dot_net.backend.booking_models import Resource
from not_dot_net.backend.db import User, session_scope


@pytest.fixture
def spy_get(monkeypatch):
    """Record every session.get(entity, ident, **kwargs) call."""
    calls: list[tuple[object, dict]] = []
    original = AsyncSession.get

    async def spy(self, entity, ident, **kwargs):
        calls.append((entity, kwargs))
        return await original(self, entity, ident, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", spy)
    return calls


async def _make_user(email: str) -> User:
    async with session_scope() as session:
        user = User(email=email, hashed_password="x", is_active=True, role="")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_delete_resource_locks_the_resource_row(spy_get):
    """I5 — the upcoming-bookings guard ran unlocked while create_booking took
    FOR UPDATE, so a booking committing in between was CASCADE-deleted with no
    error and no notification."""
    from not_dot_net.backend.booking_service import (
        create_resource, delete_resource, update_resource,
    )

    resource = await create_resource(
        name="Doomed PC", resource_type="desktop", location="Palaiseau",
    )
    await update_resource(resource.id, active=False)  # retire before deleting

    spy_get.clear()
    await delete_resource(resource.id)

    resource_gets = [kw for entity, kw in spy_get if entity is Resource]
    assert resource_gets, "delete_resource should fetch the resource via session.get"
    assert any(kw.get("with_for_update") for kw in resource_gets), (
        "delete_resource read the resource without the lock create_booking takes"
    )


async def test_overlapping_tenures_are_still_rejected():
    """The lock must not change the actual validation."""
    from not_dot_net.backend.tenure_service import add_tenure

    user = await _make_user("tenure-overlap@example.com")
    await add_tenure(
        user_id=user.id, status="PhD", employer="CNRS",
        start_date=date(2024, 1, 1), end_date=date(2024, 12, 31),
    )
    with pytest.raises(ValueError, match="overlap"):
        await add_tenure(
            user_id=user.id, status="Postdoc", employer="CNRS",
            start_date=date(2024, 6, 1), end_date=date(2025, 6, 1),
        )


async def test_non_overlapping_tenures_still_allowed():
    from not_dot_net.backend.tenure_service import add_tenure

    user = await _make_user("tenure-ok@example.com")
    await add_tenure(
        user_id=user.id, status="PhD", employer="CNRS",
        start_date=date(2020, 1, 1), end_date=date(2023, 12, 31),
    )
    second = await add_tenure(
        user_id=user.id, status="Postdoc", employer="CNRS",
        start_date=date(2024, 1, 1), end_date=None,
    )
    assert second.id is not None
