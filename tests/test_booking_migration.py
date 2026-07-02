"""Booking migration on resource deletion: delete_resource must refuse while
upcoming bookings exist, and migrate_booking moves a booking to another
resource (conflict-checked, audited, user notified)."""

import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from not_dot_net.backend.audit import AuditEvent
from not_dot_net.backend.booking_models import Booking
from not_dot_net.backend.booking_service import (
    BookingConflictError,
    BookingValidationError,
    create_booking,
    create_resource,
    delete_resource,
    migrate_booking,
    update_resource,
)
from not_dot_net.backend.db import User, session_scope
from not_dot_net.config import BookingsConfig
from tests.test_booking_service_fixes import _create_user, _setup_roles


def _valid_start(extra_days: int = 0) -> date:
    return date.today() + timedelta(days=BookingsConfig().minimum_lead_days + extra_days)


async def _resource(name: str, active: bool = True):
    r = await create_resource(name=name, resource_type="desktop", location="Palaiseau")
    if not active:
        r = await update_resource(r.id, active=False)
    return r


async def _insert_past_booking(resource_id, user_id):
    async with session_scope() as session:
        booking = Booking(
            resource_id=resource_id, user_id=user_id,
            start_date=date.today() - timedelta(days=10),
            end_date=date.today() - timedelta(days=5),
        )
        session.add(booking)
        await session.commit()


# --- delete_resource guard ---


async def test_delete_resource_blocked_by_upcoming_bookings():
    user = await _create_user(email="future@test.com")
    r = await _resource("PC-DEL", active=True)
    start = _valid_start()
    booking = await create_booking(r.id, user.id, start, start + timedelta(days=2))
    await update_resource(r.id, active=False)

    with pytest.raises(BookingValidationError, match="[Mm]igrate"):
        await delete_resource(r.id)

    async with session_scope() as session:
        assert await session.get(Booking, booking.id) is not None


async def test_delete_resource_allowed_when_all_bookings_past():
    user = await _create_user(email="past@test.com")
    r = await _resource("PC-DEL2", active=True)
    await _insert_past_booking(r.id, user.id)
    await update_resource(r.id, active=False)

    await delete_resource(r.id)  # must not raise


# --- migrate_booking ---


async def test_migrate_booking_moves_to_target_and_notifies():
    await _setup_roles()
    admin = await _create_user(email="it-mig@test.com", role="admin")
    owner = await _create_user(email="owner-mig@test.com")
    a = await _resource("PC-A")
    b = await _resource("PC-B")
    start = _valid_start()
    booking = await create_booking(a.id, owner.id, start, start + timedelta(days=2))

    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        migrated = await migrate_booking(booking.id, b.id, actor=admin)

    assert migrated.resource_id == b.id
    send.assert_awaited_once()
    to, subject, body = send.await_args.args
    assert to == "owner-mig@test.com"
    assert "PC-B" in subject or "PC-B" in body
    assert "PC-A" in body

    async with session_scope() as session:
        events = (await session.execute(
            select(AuditEvent).where(AuditEvent.category == "booking",
                                     AuditEvent.action == "migrate")
        )).scalars().all()
    assert len(events) == 1
    assert events[0].actor_id == str(admin.id)


async def test_migrate_booking_rejects_conflict_on_target():
    owner = await _create_user(email="own-c@test.com")
    other = await _create_user(email="other-c@test.com")
    a = await _resource("PC-CA")
    b = await _resource("PC-CB")
    start = _valid_start()
    booking = await create_booking(a.id, owner.id, start, start + timedelta(days=2))
    await create_booking(b.id, other.id, start, start + timedelta(days=2))

    with pytest.raises(BookingConflictError):
        await migrate_booking(booking.id, b.id)

    async with session_scope() as session:
        reloaded = await session.get(Booking, booking.id)
    assert reloaded.resource_id == a.id


async def test_migrate_booking_rejects_inactive_target():
    owner = await _create_user(email="own-i@test.com")
    a = await _resource("PC-IA")
    b = await _resource("PC-IB", active=False)
    start = _valid_start()
    booking = await create_booking(a.id, owner.id, start, start + timedelta(days=2))

    with pytest.raises(BookingValidationError, match="active"):
        await migrate_booking(booking.id, b.id)


async def test_migrate_booking_rejects_same_resource():
    owner = await _create_user(email="own-s@test.com")
    a = await _resource("PC-SA")
    start = _valid_start()
    booking = await create_booking(a.id, owner.id, start, start + timedelta(days=2))

    with pytest.raises(BookingValidationError):
        await migrate_booking(booking.id, a.id)


async def test_migrate_booking_requires_manage_bookings():
    await _setup_roles()
    staff = await _create_user(email="staff-m@test.com", role="staff")
    owner = await _create_user(email="own-p@test.com")
    a = await _resource("PC-PA")
    b = await _resource("PC-PB")
    start = _valid_start()
    booking = await create_booking(a.id, owner.id, start, start + timedelta(days=2))

    with pytest.raises(PermissionError):
        await migrate_booking(booking.id, b.id, actor=staff)


# --- migration dialog (UI) ---


async def test_migration_dialog_migrates_then_deletes(user):
    """End-to-end through the dialog: pick a target for the upcoming booking,
    click Migrate & delete → booking moved, retired resource gone."""
    from nicegui import ElementFilter, ui
    from nicegui.testing import User as UiUser  # noqa: F401 — fixture type

    from not_dot_net.backend.booking_models import Resource
    from not_dot_net.backend.booking_service import list_bookings_for_resource
    from not_dot_net.frontend.bookings import _show_migration_dialog
    from not_dot_net.frontend.i18n import t

    await _setup_roles()
    admin = await _create_user(email="it-dlg@test.com", role="admin")
    owner = await _create_user(email="owner-dlg@test.com")
    a = await _resource("PC-DLG-A")
    b = await _resource("PC-DLG-B")
    start = _valid_start()
    booking = await create_booking(a.id, owner.id, start, start + timedelta(days=2))
    await update_resource(a.id, active=False)

    @ui.page("/_migdlg")
    async def _page():
        upcoming = await list_bookings_for_resource(a.id, from_date=date.today())
        container = ui.column()
        await _show_migration_dialog(container, admin, a, upcoming)

    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock):
        await user.open("/_migdlg")
        await user.should_see(t("migrate_and_delete"))
        with user.client:
            select_el = next(el for el in ElementFilter(kind=ui.select))
            select_el.value = str(b.id)
        user.find(t("migrate_and_delete")).click()
        await user.should_see(t("bookings_migrated_resource_deleted"))

    async with session_scope() as session:
        moved = await session.get(Booking, booking.id)
        assert moved.resource_id == b.id
        assert await session.get(Resource, a.id) is None
