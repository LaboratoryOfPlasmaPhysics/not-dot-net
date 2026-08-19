"""P4 — the bookings tab must not run one session+query per resource.

Every render, date-range change, filter change and post-booking re-render cost
n_resources sessions and queries. One batched query covers them all.
"""
import uuid
from datetime import date, timedelta

import pytest

from not_dot_net.backend import booking_service
from not_dot_net.backend.booking_models import Booking, Resource
from not_dot_net.backend.db import User, session_scope


async def _make_user() -> uuid.UUID:
    async with session_scope() as session:
        user = User(
            email=f"booker-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x", is_active=True, role="",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


async def _make_resources(n: int) -> list[Resource]:
    async with session_scope() as session:
        resources = [
            Resource(name=f"Machine {i}", resource_type="workstation", location="LPP")
            for i in range(n)
        ]
        session.add_all(resources)
        await session.commit()
        for r in resources:
            await session.refresh(r)
        return resources


async def test_batch_lookup_groups_bookings_by_resource():
    from not_dot_net.backend.booking_service import list_bookings_for_resources

    resources = await _make_resources(3)
    today = date.today()
    async with session_scope() as session:
        session.add(Booking(
            resource_id=resources[0].id, user_id=await _make_user(),
            start_date=today, end_date=today + timedelta(days=2),
        ))
        session.add(Booking(
            resource_id=resources[2].id, user_id=await _make_user(),
            start_date=today + timedelta(days=1), end_date=today + timedelta(days=3),
        ))
        await session.commit()

    by_resource = await list_bookings_for_resources(
        [r.id for r in resources],
        from_date=today - timedelta(days=7),
        to_date=today + timedelta(days=7),
    )
    assert set(by_resource) == {r.id for r in resources}
    assert len(by_resource[resources[0].id]) == 1
    assert by_resource[resources[1].id] == []
    assert len(by_resource[resources[2].id]) == 1


async def test_batch_lookup_honours_the_date_window():
    from not_dot_net.backend.booking_service import list_bookings_for_resources

    resources = await _make_resources(1)
    today = date.today()
    async with session_scope() as session:
        session.add(Booking(
            resource_id=resources[0].id, user_id=await _make_user(),
            start_date=today + timedelta(days=30), end_date=today + timedelta(days=32),
        ))
        await session.commit()

    inside = await list_bookings_for_resources(
        [resources[0].id], from_date=today + timedelta(days=29), to_date=today + timedelta(days=33),
    )
    outside = await list_bookings_for_resources(
        [resources[0].id], from_date=today, to_date=today + timedelta(days=7),
    )
    assert len(inside[resources[0].id]) == 1
    assert outside[resources[0].id] == []


async def test_batch_lookup_of_nothing_does_not_query(monkeypatch):
    from not_dot_net.backend.booking_service import list_bookings_for_resources

    def boom(*a, **k):
        raise AssertionError("opened a session for an empty resource list")

    monkeypatch.setattr(booking_service, "session_scope", boom)
    assert await list_bookings_for_resources([]) == {}


async def test_availability_map_uses_one_session_for_many_resources(monkeypatch):
    """The render path itself must batch — not just have a batch helper available."""
    from not_dot_net.frontend.bookings import compute_availability

    resources = await _make_resources(5)
    today = date.today()
    async with session_scope() as session:
        session.add(Booking(
            resource_id=resources[1].id, user_id=await _make_user(),
            start_date=today, end_date=today + timedelta(days=1),
        ))
        await session.commit()

    calls = {"n": 0}
    real = booking_service.session_scope

    def counted(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(booking_service, "session_scope", counted)

    availability, conflicts = await compute_availability(
        resources, range_start=today, range_end=today + timedelta(days=2),
        setup_buffer_days=0,
    )
    assert calls["n"] == 1, f"opened {calls['n']} sessions for 5 resources"
    assert availability[resources[0].id] is True
    assert availability[resources[1].id] is False
    assert conflicts[resources[1].id].resource_id == resources[1].id


async def test_user_management_does_not_load_photo_bytes():
    """P9 — the users table never renders photos, so it must not fetch them.

    The directory still embeds photos because it displays them; they are capped
    at 256px by process_profile_photo, so the payload there is modest.
    """
    from sqlalchemy import inspect as sa_inspect

    from not_dot_net.frontend.user_management import _load_all_users

    async with session_scope() as session:
        session.add(User(
            email=f"photo-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x", is_active=True, role="", photo=b"\xff\xd8\xff" + b"x" * 4096,
        ))
        await session.commit()

    users = await _load_all_users()
    assert users
    unloaded = sa_inspect(users[0]).unloaded
    assert "photo" in unloaded, "photo column was eagerly loaded into the users table"
