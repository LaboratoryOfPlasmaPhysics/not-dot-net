"""Reproducer tests for booking service fixes: resource-row lock on create,
hand-back-day notice recipient, corrupt-status transitions, audit actor,
and status immutability via update_resource."""

import pytest
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from not_dot_net.backend.audit import AuditEvent
from not_dot_net.backend.booking_models import Resource
from not_dot_net.backend.booking_service import (
    available_transitions,
    create_booking,
    create_resource,
    set_resource_status,
    update_resource,
)
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.config import BookingsConfig


def _valid_start(extra_days: int = 0) -> date:
    return date.today() + timedelta(days=BookingsConfig().minimum_lead_days + extra_days)


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=["manage_bookings", "manage_roles", "manage_settings"],
    )
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=["create_workflows"])
    await roles_config.set(cfg)


async def _create_user(email="user@test.com", role="staff") -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _create_test_resource(**kwargs) -> Resource:
    defaults = {"name": "Test PC", "resource_type": "desktop", "location": "Palaiseau"}
    defaults.update(kwargs)
    return await create_resource(**defaults)


# --- Bug 1: create_booking must lock the resource row (phantom-insert race) ---


async def test_create_booking_locks_resource_row(monkeypatch):
    user = await _create_user()
    r = await _create_test_resource()
    start = _valid_start()

    calls: list[tuple[object, dict]] = []
    original_get = AsyncSession.get

    async def spy_get(self, entity, ident, **kwargs):
        calls.append((entity, kwargs))
        return await original_get(self, entity, ident, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", spy_get)
    await create_booking(r.id, user.id, start, start + timedelta(days=2))

    resource_gets = [kw for entity, kw in calls if entity is Resource]
    assert resource_gets, "create_booking should fetch the resource via session.get"
    assert any(kw.get("with_for_update") for kw in resource_gets)


# --- Bug 2: RETURNED notice on the hand-back day goes to the ending booking's user ---


async def test_returned_on_handback_day_notifies_ending_booking_user():
    await _setup_roles()
    admin = await _create_user(email="it-ret@test.com", role="admin")
    owner = await _create_user(email="owner-ret@test.com", role="staff")
    other = await _create_user(email="other-ret@test.com", role="staff")
    r = await _create_test_resource(name="PC-RETURN")
    start = _valid_start()
    end = start + timedelta(days=3)
    await create_booking(r.id, owner.id, start, end, actor=owner)
    # An upcoming booking by someone else must NOT receive the return notice.
    next_start = end + timedelta(days=7)
    await create_booking(r.id, other.id, next_start, next_start + timedelta(days=2), actor=other)

    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        await set_resource_status(r.id, "ready", actor=admin, today=start)
        await set_resource_status(r.id, "in_use", actor=admin, today=start)
        send.reset_mock()
        # end_date is the hand-back day: marking RETURNED on that day must
        # notify the booking that just ended.
        await set_resource_status(r.id, "returned", actor=admin, today=end)

    send.assert_awaited_once()
    assert send.await_args.args[0] == "owner-ret@test.com"


# --- Bug 3: available_transitions must not crash on a corrupt status ---


def test_available_transitions_unknown_status_returns_empty():
    assert available_transitions("corrupt-status") == []


# --- Bug 4: booking-create audit logs the actor, not the beneficiary ---


async def test_create_booking_audit_records_actor_not_beneficiary():
    await _setup_roles()
    manager = await _create_user(email="mgr-audit@test.com", role="admin")
    beneficiary = await _create_user(email="user-audit@test.com", role="staff")
    r = await _create_test_resource(name="PC-AUDIT")
    start = _valid_start()

    await create_booking(r.id, beneficiary.id, start, start + timedelta(days=2), actor=manager)

    async with session_scope() as session:
        result = await session.execute(
            select(AuditEvent).where(AuditEvent.category == "booking",
                                     AuditEvent.action == "create")
        )
        event = result.scalars().one()
    assert event.actor_id == str(manager.id)


async def test_create_booking_audit_falls_back_to_user_without_actor():
    user = await _create_user(email="self-audit@test.com")
    r = await _create_test_resource(name="PC-AUDIT2")
    start = _valid_start()

    await create_booking(r.id, user.id, start, start + timedelta(days=2))

    async with session_scope() as session:
        result = await session.execute(
            select(AuditEvent).where(AuditEvent.category == "booking",
                                     AuditEvent.action == "create")
        )
        event = result.scalars().one()
    assert event.actor_id == str(user.id)


# --- Test gap 5: status is not updatable through update_resource ---


async def test_update_resource_rejects_status_field():
    r = await _create_test_resource(name="PC-IMMUT")
    with pytest.raises(ValueError, match="Cannot update field"):
        await update_resource(r.id, status="in_use")
    fetched_status = (await update_resource(r.id, name="PC-IMMUT")).status
    assert fetched_status == "available"
