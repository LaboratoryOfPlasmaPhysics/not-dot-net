"""D2: service-layer authorization must be mandatory, not opt-in.

Every mutating service function used to read `if actor is not None:
check_permission(...)`, so a caller that simply omitted the argument got no
authorization at all. These tests pin the inverse: omitting the actor is
refused, and a non-privileged actor is refused too.
"""
import uuid
from datetime import date, timedelta

import pytest

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.roles import RoleDefinition, roles_config


async def _user(email: str, *, superuser: bool = False, role: str = "nobody") -> User:
    async with session_scope() as session:
        u = User(id=uuid.uuid4(), email=email, hashed_password="x",
                 role=role, is_superuser=superuser)
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u


async def _no_permission_role() -> None:
    cfg = await roles_config.get()
    cfg.roles["nobody"] = RoleDefinition(label="Nobody", permissions=[])
    await roles_config.set(cfg)


@pytest.fixture
async def actors():
    await _no_permission_role()
    return {
        "root": await _user("root@d2.test", superuser=True),
        "plain": await _user("plain@d2.test"),
    }


# --- bookings -----------------------------------------------------------------


async def test_create_resource_refuses_without_an_actor(actors):
    from not_dot_net.backend.booking_service import create_resource
    with pytest.raises(PermissionError):
        await create_resource(name="ghost", resource_type="desktop")


async def test_create_resource_refuses_an_unprivileged_actor(actors):
    from not_dot_net.backend.booking_service import create_resource
    with pytest.raises(PermissionError):
        await create_resource(name="ghost", resource_type="desktop", actor=actors["plain"])


async def test_resource_mutators_refuse_without_an_actor(actors):
    from not_dot_net.backend import booking_service as bs
    res = await bs.create_resource(name="lab-pc", resource_type="desktop", actor=actors["root"])

    with pytest.raises(PermissionError):
        await bs.update_resource(res.id, name="renamed")
    with pytest.raises(PermissionError):
        await bs.delete_resource(res.id)
    with pytest.raises(PermissionError):
        await bs.restore_resource(res.id)
    with pytest.raises(PermissionError):
        await bs.set_resource_status(res.id, "out_of_service")


async def test_create_booking_refuses_without_an_actor(actors):
    from not_dot_net.backend import booking_service as bs
    res = await bs.create_resource(name="scope", resource_type="desktop", actor=actors["root"])
    start = date.today() + timedelta(days=30)
    with pytest.raises(PermissionError):
        await bs.create_booking(res.id, actors["plain"].id, start, start + timedelta(days=1))


async def test_migrate_booking_refuses_without_an_actor(actors):
    from not_dot_net.backend import booking_service as bs
    with pytest.raises(PermissionError):
        await bs.migrate_booking(uuid.uuid4(), uuid.uuid4())


# --- office availability ------------------------------------------------------


async def test_office_availability_refuses_without_an_actor(actors):
    from not_dot_net.backend import booking_service as bs
    from not_dot_net.backend.office_availability import offer_availability, revoke_availability

    office = await bs.create_resource(
        name="B-210", resource_type="office", actor=actors["root"],
        owner_user_id=actors["plain"].id,
    )
    start = date.today() + timedelta(days=30)
    with pytest.raises(PermissionError):
        await offer_availability(office.id, start, start + timedelta(days=5))

    window = await offer_availability(
        office.id, start, start + timedelta(days=5), actor=actors["root"],
    )
    with pytest.raises(PermissionError):
        await revoke_availability(window.id)


# --- floor plans --------------------------------------------------------------


async def test_floorplan_mutators_refuse_without_an_actor(actors):
    from not_dot_net.backend import floorplan_service as fs
    with pytest.raises(PermissionError):
        await fs.create_floor_plan("plan", b"not-an-image")
    with pytest.raises(PermissionError):
        await fs.delete_floor_plan(uuid.uuid4())
    with pytest.raises(PermissionError):
        await fs.add_map_point(uuid.uuid4(), "pin", "room", 1, 2)
    with pytest.raises(PermissionError):
        await fs.delete_map_point(uuid.uuid4())
    with pytest.raises(PermissionError):
        await fs.update_map_point_geometry(uuid.uuid4(), [[0, 0], [1, 1]])


# --- pages --------------------------------------------------------------------


async def test_page_mutators_refuse_without_an_actor(actors):
    from not_dot_net.backend import page_service as ps
    with pytest.raises(PermissionError):
        await ps.create_page(title="T", slug="t", content="c", author_id=None)
    with pytest.raises(PermissionError):
        await ps.update_page(uuid.uuid4(), title="x")
    with pytest.raises(PermissionError):
        await ps.delete_page(uuid.uuid4())


async def test_page_mutators_refuse_an_unprivileged_actor(actors):
    from not_dot_net.backend import page_service as ps
    with pytest.raises(PermissionError):
        await ps.create_page(title="T", slug="t2", content="c",
                             author_id=None, actor=actors["plain"])


# --- tenures ------------------------------------------------------------------


async def test_tenure_mutators_refuse_without_an_actor(actors):
    from not_dot_net.backend import tenure_service as ts
    with pytest.raises(PermissionError):
        await ts.add_tenure(user_id=actors["plain"].id, status="PhD",
                            employer="CNRS", start_date=date.today())
    with pytest.raises(PermissionError):
        await ts.update_tenure(uuid.uuid4(), status="x")
    with pytest.raises(PermissionError):
        await ts.delete_tenure(uuid.uuid4())
    with pytest.raises(PermissionError):
        await ts.close_tenure(uuid.uuid4(), date.today())


async def test_system_actor_is_accepted_where_no_user_is_acting(actors):
    """The workflow engine records tenures with no acting user — that path must
    stay open, but only through an explicitly named actor."""
    from not_dot_net.backend.permissions import SYSTEM_ACTOR
    from not_dot_net.backend.tenure_service import add_tenure, list_tenures

    await add_tenure(user_id=actors["plain"].id, status="PhD", employer="CNRS",
                     start_date=date.today(), actor=SYSTEM_ACTOR)
    assert len(await list_tenures(actors["plain"].id)) == 1


# --- profile photos and bulk import (same class, found by sweeping for the pattern) ---


async def test_profile_photo_refuses_without_an_actor(actors):
    from not_dot_net.backend.profile_photo import remove_profile_photo, save_profile_photo
    with pytest.raises(PermissionError):
        await save_profile_photo(actors["plain"].id, b"x")
    with pytest.raises(PermissionError):
        await remove_profile_photo(actors["plain"].id)


async def test_bulk_import_refuses_without_an_actor(actors):
    from not_dot_net.backend.data_io import import_all
    with pytest.raises(PermissionError):
        await import_all({"pages": []})
    with pytest.raises(PermissionError):
        await import_all({"pages": []}, actor=actors["plain"])


async def test_no_service_still_treats_a_missing_actor_as_permission_to_proceed():
    """Guard against the pattern coming back: `if actor is not None: check(...)`
    grants full access to any caller that omits the argument."""
    import re
    from pathlib import Path

    offenders = []
    for path in Path("not_dot_net/backend").glob("*.py"):
        src = path.read_text()
        for match in re.finditer(r"if actor is not None:\n\s+await check_permission", src):
            offenders.append(f"{path.name}:{src[:match.start()].count(chr(10)) + 1}")
    assert not offenders, f"opt-in authorization reintroduced: {offenders}"
