"""Tests for booking service — resource CRUD and reservation management."""

from not_dot_net.backend.permissions import SYSTEM_ACTOR
import pytest
import uuid
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from not_dot_net.backend.booking_service import (
    BookingConflictError,
    BookingValidationError,
    available_transitions,
    cancel_booking,
    create_booking,
    create_resource,
    delete_resource,
    get_resource_by_id,
    list_bookings_for_resource,
    list_bookings_for_user,
    list_resources,
    restore_resource,
    set_resource_status,
    update_resource,
)
from not_dot_net.backend.booking_models import Booking, Resource, ResourceStatus
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.config import BookingsConfig, bookings_config

def _valid_start(extra_days: int = 0) -> date:
    return date.today() + timedelta(days=BookingsConfig().minimum_lead_days + extra_days)


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=["manage_bookings", "manage_roles", "manage_settings"],
    )
    cfg.roles["staff"] = RoleDefinition(
        label="Staff",
        permissions=["create_workflows"],
    )
    await roles_config.set(cfg)


async def _create_user(email="user@test.com", role="staff") -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _create_test_resource(**kwargs) -> Resource:
    defaults = {"name": "Test PC", "resource_type": "desktop", "location": "Palaiseau",
                "actor": SYSTEM_ACTOR}
    defaults.update(kwargs)
    return await create_resource(**defaults)


# --- Resource CRUD ---


async def test_create_and_list_resources():
    await _create_test_resource(name="PC-01")
    await _create_test_resource(name="PC-02")
    resources = await list_resources()
    assert len(resources) == 2
    assert {r.name for r in resources} == {"PC-01", "PC-02"}


async def test_list_resources_active_only():
    r = await _create_test_resource()
    await update_resource(r.id, active=False, actor=SYSTEM_ACTOR)
    assert len(await list_resources(active_only=True)) == 0
    assert len(await list_resources(active_only=False)) == 1


async def test_update_resource():
    r = await _create_test_resource()
    updated = await update_resource(r.id, name="New Name", location="Jussieu", actor=SYSTEM_ACTOR)
    assert updated.name == "New Name"
    assert updated.location == "Jussieu"


async def test_create_resource_with_owner_user_id():
    owner = await _create_user(email="owner2@test.com")
    resource = await create_resource(
        name="Room 101", resource_type="office", location="Palaiseau",
        owner_user_id=owner.id, actor=SYSTEM_ACTOR
    )
    assert resource.owner_user_id == owner.id


async def test_update_resource_owner_user_id():
    owner = await _create_user(email="owner3@test.com")
    r = await _create_test_resource(name="Room 102", resource_type="office")
    updated = await update_resource(r.id, owner_user_id=owner.id, actor=SYSTEM_ACTOR)
    assert updated.owner_user_id == owner.id


async def test_update_nonexistent_resource():
    with pytest.raises(ValueError, match="not found"):
        await update_resource(uuid.uuid4(), name="X", actor=SYSTEM_ACTOR)


async def test_update_resource_rejects_immutable_fields():
    r = await _create_test_resource()
    with pytest.raises(ValueError, match="Cannot update field"):
        await update_resource(r.id, id=uuid.uuid4(), actor=SYSTEM_ACTOR)
    with pytest.raises(ValueError, match="Cannot update field"):
        await update_resource(r.id, created_at=date.today(), actor=SYSTEM_ACTOR)


async def test_delete_resource():
    r = await _create_test_resource()
    await update_resource(r.id, active=False, actor=SYSTEM_ACTOR)  # retire before deleting
    await delete_resource(r.id, actor=SYSTEM_ACTOR)
    assert await get_resource_by_id(r.id) is None


async def test_delete_nonexistent_resource():
    with pytest.raises(ValueError, match="not found"):
        await delete_resource(uuid.uuid4(), actor=SYSTEM_ACTOR)


async def test_get_resource_by_id():
    r = await _create_test_resource()
    found = await get_resource_by_id(r.id)
    assert found is not None
    assert found.name == r.name


async def test_get_resource_by_id_not_found():
    assert await get_resource_by_id(uuid.uuid4()) is None


# --- Booking validation ---


async def test_create_booking_success():
    user = await _create_user()
    r = await _create_test_resource()
    tomorrow = _valid_start()
    end = tomorrow + timedelta(days=3)
    b = await create_booking(r.id, user.id, tomorrow, end, note="Test", actor=SYSTEM_ACTOR)
    assert b.resource_id == r.id
    assert b.start_date == tomorrow
    assert b.end_date == end


async def test_create_booking_rejects_missing_resource():
    user = await _create_user()
    tomorrow = _valid_start()
    with pytest.raises(ValueError, match="not found"):
        await create_booking(uuid.uuid4(), user.id, tomorrow, tomorrow + timedelta(days=1), actor=SYSTEM_ACTOR)


async def test_create_booking_rejects_inactive_resource():
    user = await _create_user()
    r = await _create_test_resource()
    r = await update_resource(r.id, active=False, actor=SYSTEM_ACTOR)
    tomorrow = _valid_start()
    with pytest.raises(BookingValidationError, match="not active"):
        await create_booking(r.id, user.id, tomorrow, tomorrow + timedelta(days=1), actor=SYSTEM_ACTOR)


async def test_booking_end_before_start():
    user = await _create_user()
    r = await _create_test_resource()
    tomorrow = _valid_start()
    with pytest.raises(BookingValidationError, match="End date"):
        await create_booking(r.id, user.id, tomorrow, tomorrow, actor=SYSTEM_ACTOR)


async def test_booking_in_the_past():
    user = await _create_user()
    r = await _create_test_resource()
    yesterday = date.today() - timedelta(days=1)
    with pytest.raises(BookingValidationError, match="past"):
        await create_booking(r.id, user.id, yesterday, date.today() + timedelta(days=1), actor=SYSTEM_ACTOR)


async def test_booking_requires_seven_days_notice():
    user = await _create_user()
    r = await _create_test_resource()
    start = date.today() + timedelta(days=6)
    with pytest.raises(BookingValidationError, match="at least 7 days"):
        await create_booking(r.id, user.id, start, start + timedelta(days=3), actor=SYSTEM_ACTOR)


async def test_booking_minimum_lead_days_uses_bookings_config():
    user = await _create_user()
    r = await _create_test_resource()
    await bookings_config.set(BookingsConfig(minimum_lead_days=3))
    try:
        start = date.today() + timedelta(days=2)
        with pytest.raises(BookingValidationError, match="at least 3 days"):
            await create_booking(r.id, user.id, start, start + timedelta(days=3), actor=SYSTEM_ACTOR)
        booking = await create_booking(
            r.id,
            user.id,
            date.today() + timedelta(days=3),
            date.today() + timedelta(days=6), actor=SYSTEM_ACTOR
        )
    finally:
        await bookings_config.reset()

    assert booking.start_date == date.today() + timedelta(days=3)


async def test_booking_exceeds_max_days():
    user = await _create_user()
    r = await _create_test_resource()
    start = _valid_start()
    end = start + timedelta(days=200)
    with pytest.raises(BookingValidationError, match="exceed"):
        await create_booking(r.id, user.id, start, end, actor=SYSTEM_ACTOR)


async def test_booking_max_days_uses_bookings_config():
    user = await _create_user()
    r = await _create_test_resource()
    await bookings_config.set(BookingsConfig(max_booking_days=4))
    try:
        start = _valid_start()
        with pytest.raises(BookingValidationError, match="exceed 4 days"):
            await create_booking(r.id, user.id, start, start + timedelta(days=5), actor=SYSTEM_ACTOR)
        booking = await create_booking(r.id, user.id, start, start + timedelta(days=4), actor=SYSTEM_ACTOR)
    finally:
        await bookings_config.reset()

    assert booking.end_date == start + timedelta(days=4)


# --- Booking conflicts ---


async def test_booking_conflict():
    user = await _create_user()
    r = await _create_test_resource()
    start = _valid_start()
    end = start + timedelta(days=5)
    await create_booking(r.id, user.id, start, end, actor=SYSTEM_ACTOR)

    user2 = await _create_user(email="user2@test.com")
    overlap_start = start + timedelta(days=2)
    overlap_end = end + timedelta(days=2)
    with pytest.raises(BookingConflictError):
        await create_booking(r.id, user2.id, overlap_start, overlap_end, actor=SYSTEM_ACTOR)


async def test_booking_rejects_adjacent_booking_without_setup_buffer():
    user = await _create_user()
    r = await _create_test_resource()
    start = _valid_start()
    mid = start + timedelta(days=5)
    end = mid + timedelta(days=5)
    await create_booking(r.id, user.id, start, mid, actor=SYSTEM_ACTOR)
    with pytest.raises(BookingConflictError, match="setup buffer"):
        await create_booking(r.id, user.id, mid, end, actor=SYSTEM_ACTOR)


async def test_booking_allows_after_resource_setup_buffer():
    user = await _create_user()
    r = await _create_test_resource()
    start = _valid_start()
    first_end = start + timedelta(days=5)
    second_start = first_end + timedelta(days=7)
    await create_booking(r.id, user.id, start, first_end, actor=SYSTEM_ACTOR)
    b2 = await create_booking(r.id, user.id, second_start, second_start + timedelta(days=5), actor=SYSTEM_ACTOR)
    assert b2.start_date == second_start


async def test_booking_setup_buffer_uses_bookings_config():
    user = await _create_user()
    r = await _create_test_resource()
    start = _valid_start()
    first_end = start + timedelta(days=5)
    await bookings_config.set(BookingsConfig(resource_setup_buffer_days=2))
    try:
        await create_booking(r.id, user.id, start, first_end, actor=SYSTEM_ACTOR)
        with pytest.raises(BookingConflictError, match="2-day setup buffer"):
            await create_booking(
                r.id,
                user.id,
                first_end + timedelta(days=1),
                first_end + timedelta(days=3), actor=SYSTEM_ACTOR
            )
        b2 = await create_booking(
            r.id,
            user.id,
            first_end + timedelta(days=2),
            first_end + timedelta(days=4), actor=SYSTEM_ACTOR
        )
    finally:
        await bookings_config.reset()

    assert b2.start_date == first_end + timedelta(days=2)


async def test_booking_setup_buffer_is_per_resource():
    user = await _create_user()
    r1 = await _create_test_resource(name="PC-01")
    r2 = await _create_test_resource(name="PC-02")
    start = _valid_start()
    end = start + timedelta(days=5)
    await create_booking(r1.id, user.id, start, end, actor=SYSTEM_ACTOR)
    b2 = await create_booking(r2.id, user.id, start + timedelta(days=1), end + timedelta(days=1), actor=SYSTEM_ACTOR)
    assert b2.resource_id == r2.id


# --- Booking listing ---


async def test_list_bookings_for_resource():
    user = await _create_user()
    r = await _create_test_resource()
    start = _valid_start()
    end = start + timedelta(days=3)
    await create_booking(r.id, user.id, start, end, actor=SYSTEM_ACTOR)
    bookings = await list_bookings_for_resource(r.id)
    assert len(bookings) == 1


async def test_list_bookings_for_user():
    user = await _create_user()
    r = await _create_test_resource()
    start = _valid_start()
    end = start + timedelta(days=3)
    await create_booking(r.id, user.id, start, end, actor=SYSTEM_ACTOR)
    bookings = await list_bookings_for_user(user.id)
    assert len(bookings) == 1


# --- Booking cancellation ---


async def test_cancel_own_booking():
    user = await _create_user()
    r = await _create_test_resource()
    start = _valid_start()
    end = start + timedelta(days=3)
    b = await create_booking(r.id, user.id, start, end, actor=SYSTEM_ACTOR)
    await cancel_booking(b.id, actor=user)
    assert len(await list_bookings_for_resource(r.id)) == 0


async def test_cancel_other_user_booking_rejected():
    user1 = await _create_user(email="u1@test.com")
    user2 = await _create_user(email="u2@test.com")
    r = await _create_test_resource()
    start = _valid_start()
    b = await create_booking(r.id, user1.id, start, start + timedelta(days=3), actor=SYSTEM_ACTOR)
    with pytest.raises(PermissionError):
        await cancel_booking(b.id, actor=user2)


async def test_cancel_as_admin():
    await _setup_roles()
    user1 = await _create_user(email="u1@test.com")
    admin = await _create_user(email="admin@test.com", role="admin")
    r = await _create_test_resource()
    start = _valid_start()
    b = await create_booking(r.id, user1.id, start, start + timedelta(days=3), actor=SYSTEM_ACTOR)
    await cancel_booking(b.id, actor=admin)
    assert len(await list_bookings_for_resource(r.id)) == 0


async def test_cancel_nonexistent_booking():
    user = await _create_user()
    with pytest.raises(ValueError, match="not found"):
        await cancel_booking(uuid.uuid4(), actor=user)


# --- OS/software ---


async def test_booking_with_os_and_software():
    user = await _create_user()
    r = await _create_test_resource()
    start = _valid_start()
    b = await create_booking(
        r.id, user.id, start, start + timedelta(days=3),
        os_choice="Ubuntu", software_tags=["Python", "GCC"], actor=SYSTEM_ACTOR
    )
    assert b.os_choice == "Ubuntu"
    assert b.software_tags == ["Python", "GCC"]


async def test_create_booking_actor_can_only_book_for_self():
    await _setup_roles()
    user1 = await _create_user(email="u1@test.com", role="staff")
    user2 = await _create_user(email="u2@test.com", role="staff")
    r = await _create_test_resource()
    start = _valid_start()

    with pytest.raises(PermissionError):
        await create_booking(
            r.id,
            user2.id,
            start,
            start + timedelta(days=3),
            actor=user1,
        )


async def test_create_booking_actor_manager_can_book_for_other_user():
    await _setup_roles()
    admin = await _create_user(email="admin-book@test.com", role="admin")
    user = await _create_user(email="user-book@test.com", role="staff")
    r = await _create_test_resource()
    start = _valid_start()

    booking = await create_booking(
        r.id,
        user.id,
        start,
        start + timedelta(days=3),
        actor=admin,
    )

    assert booking.user_id == user.id


# --- Permission enforcement ---


async def test_create_resource_requires_permission():
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    with pytest.raises(PermissionError):
        await create_resource("PC", "desktop", actor=staff)


async def test_create_resource_allowed_with_permission():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    r = await create_resource("PC", "desktop", actor=admin)
    assert r.name == "PC"


async def test_update_resource_requires_permission():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    r = await create_resource("PC", "desktop", actor=admin)
    staff = await _create_user(email="staff@test.com", role="staff")
    with pytest.raises(PermissionError):
        await update_resource(r.id, actor=staff, name="New")


async def test_delete_resource_requires_permission():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    r = await create_resource("PC", "desktop", actor=admin)
    staff = await _create_user(email="staff@test.com", role="staff")
    with pytest.raises(PermissionError):
        await delete_resource(r.id, actor=staff)


async def test_cancel_booking_with_actor_admin():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    user1 = await _create_user(email="u1@test.com", role="staff")
    r = await create_resource("PC", "desktop", actor=admin)
    start = _valid_start()
    b = await create_booking(r.id, user1.id, start, start + timedelta(days=3), actor=SYSTEM_ACTOR)
    await cancel_booking(b.id, actor=admin)
    assert len(await list_bookings_for_resource(r.id)) == 0


async def test_cancel_booking_non_owner_non_admin_rejected():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    user1 = await _create_user(email="u1@test.com", role="staff")
    user2 = await _create_user(email="u2@test.com", role="staff")
    r = await create_resource("PC", "desktop", actor=admin)
    start = _valid_start()
    b = await create_booking(r.id, user1.id, start, start + timedelta(days=3), actor=SYSTEM_ACTOR)
    with pytest.raises(PermissionError):
        await cancel_booking(b.id, actor=user2)


# --- Resource status lifecycle ---


async def test_new_resource_defaults_to_available_status():
    r = await _create_test_resource(name="PC-STATUS")
    fetched = await get_resource_by_id(r.id)
    assert fetched.status == "available"


async def test_set_resource_status_legal_transition():
    await _setup_roles()
    admin = await _create_user(email="it@test.com", role="admin")
    r = await _create_test_resource(name="PC-FSM")
    updated = await set_resource_status(r.id, "ready", actor=admin)
    assert updated.status == "ready"


async def test_set_resource_status_illegal_transition_raises():
    await _setup_roles()
    admin = await _create_user(email="it2@test.com", role="admin")
    r = await _create_test_resource(name="PC-FSM2")
    # available → in_use is not allowed (must go via ready)
    with pytest.raises(BookingValidationError):
        await set_resource_status(r.id, "in_use", actor=admin)


async def test_set_resource_status_requires_permission():
    await _setup_roles()
    plain = await _create_user(email="plain@test.com", role="staff")
    r = await _create_test_resource(name="PC-FSM3")
    with pytest.raises(PermissionError):
        await set_resource_status(r.id, "ready", actor=plain)


def test_available_transitions_lists_legal_next_states():
    assert available_transitions("in_use") == ["out_of_service", "returned"]
    assert available_transitions("out_of_service") == ["available"]


# --- Status-change notifications ---


def _recipients(send_mock) -> set[str]:
    return {call.args[0] for call in send_mock.await_args_list}


async def test_ready_notifies_current_booking_user():
    await _setup_roles()
    admin = await _create_user(email="it-r@test.com", role="admin")
    owner = await _create_user(email="owner-r@test.com", role="staff")
    r = await _create_test_resource(name="PC-NOTIFY")
    start = _valid_start()
    await create_booking(r.id, owner.id, start, start + timedelta(days=3), actor=owner)
    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        # today inside the booking window so it is the "current" booking
        await set_resource_status(r.id, "ready", actor=admin, today=start)
    send.assert_awaited_once()
    assert send.await_args.args[0] == "owner-r@test.com"


async def test_ready_with_no_booking_sends_no_email():
    await _setup_roles()
    admin = await _create_user(email="it-n@test.com", role="admin")
    r = await _create_test_resource(name="PC-NOBOOK")
    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        await set_resource_status(r.id, "ready", actor=admin)
    send.assert_not_awaited()


async def test_out_of_service_notifies_managers():
    await _setup_roles()
    admin = await _create_user(email="mgr@test.com", role="admin")
    r = await _create_test_resource(name="PC-OOS")
    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        await set_resource_status(r.id, "out_of_service", actor=admin)
    assert "mgr@test.com" in _recipients(send)


async def test_out_of_service_dedupes_manager_who_is_also_booker():
    await _setup_roles()
    # admin is both a manage_bookings manager AND the resource's booker:
    # the dedup must collapse the two roles to a single email.
    admin = await _create_user(email="mgrbook@test.com", role="admin")
    r = await _create_test_resource(name="PC-OOS-DEDUP")
    start = _valid_start()
    await create_booking(r.id, admin.id, start, start + timedelta(days=2), actor=admin)
    with patch("not_dot_net.backend.booking_service.send_mail", new_callable=AsyncMock) as send:
        await set_resource_status(r.id, "out_of_service", actor=admin)
    assert send.await_count == 1
    assert send.await_args.args[0] == "mgrbook@test.com"


# --- Two-stage deletion ---


async def test_delete_active_resource_is_blocked():
    await _setup_roles()
    admin = await _create_user(email="del1@test.com", role="admin")
    r = await _create_test_resource(name="PC-DEL")  # active by default
    with pytest.raises(BookingValidationError):
        await delete_resource(r.id, actor=admin)


async def test_delete_retired_resource_succeeds():
    await _setup_roles()
    admin = await _create_user(email="del2@test.com", role="admin")
    r = await _create_test_resource(name="PC-DEL2")
    await update_resource(r.id, active=False, actor=admin)  # retire
    await delete_resource(r.id, actor=admin)
    assert await get_resource_by_id(r.id) is None


async def test_restore_reactivates_and_resets_status():
    await _setup_roles()
    admin = await _create_user(email="res1@test.com", role="admin")
    r = await _create_test_resource(name="PC-RES")
    await set_resource_status(r.id, "out_of_service", actor=admin)
    await update_resource(r.id, active=False, actor=admin)  # retire
    restored = await restore_resource(r.id, actor=admin)
    assert restored.active is True
    assert restored.status == "available"


async def test_out_of_service_does_not_block_booking():
    await _setup_roles()
    admin = await _create_user(email="oosb@test.com", role="admin")
    owner = await _create_user(email="oosu@test.com", role="staff")
    r = await _create_test_resource(name="PC-OOSB")
    await set_resource_status(r.id, "out_of_service", actor=admin)
    start = _valid_start()
    booking = await create_booking(r.id, owner.id, start, start + timedelta(days=2), actor=owner)
    assert booking.id is not None


from not_dot_net.backend.office_availability import offer_availability


async def test_create_booking_office_inside_window_succeeds():
    await _setup_roles()
    admin = await _create_user(email="office-admin@test.com", role="admin")
    user = await _create_user(email="booker2@test.com")
    resource = await _create_test_resource(name="Room 201", resource_type="office")
    start = _valid_start()
    await offer_availability(resource.id, start, start + timedelta(days=20), actor=admin)
    booking = await create_booking(resource.id, user.id, start, start + timedelta(days=3), actor=user)
    assert booking.resource_id == resource.id


async def test_create_booking_office_outside_window_raises():
    await _setup_roles()
    admin = await _create_user(email="office-admin2@test.com", role="admin")
    user = await _create_user(email="booker3@test.com")
    resource = await _create_test_resource(name="Room 202", resource_type="office")
    start = _valid_start()
    await offer_availability(resource.id, start, start + timedelta(days=3), actor=admin)
    with pytest.raises(BookingValidationError):
        await create_booking(resource.id, user.id, start, start + timedelta(days=10), actor=user)


async def test_create_booking_office_with_no_windows_raises():
    await _setup_roles()
    user = await _create_user(email="booker4@test.com")
    resource = await _create_test_resource(name="Room 203", resource_type="office")
    start = _valid_start()
    with pytest.raises(BookingValidationError):
        await create_booking(resource.id, user.id, start, start + timedelta(days=3), actor=user)


async def test_create_booking_equipment_unaffected_by_office_check():
    """No availability window exists anywhere, but equipment booking must
    still succeed — is_covered is never consulted for non-office resources."""
    r = await _create_test_resource(name="PC-99", resource_type="desktop")
    pc_user = await _create_user(email="pcuser@test.com")
    start = _valid_start()
    booking = await create_booking(r.id, pc_user.id, start, start + timedelta(days=3), actor=SYSTEM_ACTOR)
    assert booking.resource_id == r.id
