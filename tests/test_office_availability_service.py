import uuid
from datetime import date

import pytest

from not_dot_net.backend.booking_models import Booking, Resource
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.office_availability import (
    OfficeAvailability,
    OfficeAvailabilityError,
    is_covered,
    list_availability_windows,
    offer_availability,
    revoke_availability,
)
from not_dot_net.backend.roles import RoleDefinition, roles_config

_RESOURCE = uuid.uuid4()
_USER = uuid.uuid4()


def _window(start: str, end: str) -> OfficeAvailability:
    return OfficeAvailability(
        resource_id=_RESOURCE, start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end), offered_by=_USER,
    )


def test_is_covered_empty_window_list():
    assert is_covered([], date(2026, 8, 1), date(2026, 8, 10)) is False


def test_is_covered_exact_match():
    windows = [_window("2026-08-01", "2026-08-10")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is True


def test_is_covered_fully_inside_a_wider_window():
    windows = [_window("2026-07-25", "2026-08-20")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is True


def test_is_covered_partially_outside_returns_false():
    windows = [_window("2026-08-01", "2026-08-05")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is False


def test_is_covered_gap_between_windows_returns_false():
    windows = [_window("2026-08-01", "2026-08-04"), _window("2026-08-06", "2026-08-10")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is False


def test_is_covered_multiple_overlapping_windows_union():
    windows = [
        _window("2026-08-01", "2026-08-04"),
        _window("2026-08-03", "2026-08-07"),
        _window("2026-08-06", "2026-08-10"),
    ]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is True


def test_is_covered_unordered_windows_still_evaluated_correctly():
    windows = [_window("2026-08-06", "2026-08-10"), _window("2026-08-01", "2026-08-06")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is True


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(label="Admin", permissions=["manage_bookings"])
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=["create_workflows"])
    await roles_config.set(cfg)


async def _create_user(email="user@test.com", role="staff") -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _create_office(owner=None) -> Resource:
    async with session_scope() as session:
        resource = Resource(
            name="Room 101", resource_type="office", location="Palaiseau",
            owner_user_id=owner.id if owner else None,
        )
        session.add(resource)
        await session.commit()
        await session.refresh(resource)
        return resource


async def test_offer_availability_by_owner():
    await _setup_roles()
    owner = await _create_user(email="owner@test.com")
    resource = await _create_office(owner=owner)
    window = await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=owner)
    assert window.resource_id == resource.id
    assert window.offered_by == owner.id


async def test_offer_availability_by_manager():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    resource = await _create_office()
    window = await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=admin)
    assert window.offered_by == admin.id


async def test_offer_availability_denied_for_non_owner_non_manager():
    await _setup_roles()
    owner = await _create_user(email="owner@test.com")
    stranger = await _create_user(email="stranger@test.com")
    resource = await _create_office(owner=owner)
    with pytest.raises(PermissionError):
        await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=stranger)


async def test_offer_availability_rejects_inverted_range():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    resource = await _create_office()
    with pytest.raises(OfficeAvailabilityError):
        await offer_availability(resource.id, date(2026, 8, 15), date(2026, 8, 1), actor=admin)


async def test_list_availability_windows_returns_offered_windows():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    resource = await _create_office()
    await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=admin)
    windows = await list_availability_windows(resource.id)
    assert len(windows) == 1


async def test_revoke_availability_by_owner():
    await _setup_roles()
    owner = await _create_user(email="owner@test.com")
    resource = await _create_office(owner=owner)
    window = await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=owner)
    await revoke_availability(window.id, actor=owner)
    assert await list_availability_windows(resource.id) == []


async def test_revoke_availability_denied_for_non_owner_non_manager():
    await _setup_roles()
    owner = await _create_user(email="owner@test.com")
    stranger = await _create_user(email="stranger@test.com")
    resource = await _create_office(owner=owner)
    window = await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=owner)
    with pytest.raises(PermissionError):
        await revoke_availability(window.id, actor=stranger)


async def test_revoke_availability_blocked_by_booking_inside_window():
    await _setup_roles()
    admin = await _create_user(email="admin2@test.com", role="admin")
    booker = await _create_user(email="booker@test.com")
    resource = await _create_office()
    window = await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=admin)

    async with session_scope() as session:
        booking = Booking(
            resource_id=resource.id, user_id=booker.id,
            start_date=date(2026, 8, 3), end_date=date(2026, 8, 6),
        )
        session.add(booking)
        await session.commit()

    with pytest.raises(OfficeAvailabilityError):
        await revoke_availability(window.id, actor=admin)
