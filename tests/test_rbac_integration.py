"""End-to-end RBAC integration test — roles, permissions, enforcement."""

import uuid
from datetime import date, timedelta
import pytest

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import (
    get_permissions,
    has_permissions,
    check_permission,
)
from not_dot_net.backend.roles import RoleDefinition, roles_config, seed_admin_permissions
from not_dot_net.backend.booking_service import (
    create_resource,
    delete_resource,
    update_resource,
    create_booking,
    cancel_booking,
)
from not_dot_net.backend.workflow_service import create_request, submit_step


# Force permission registration
import not_dot_net.backend.booking_service  # noqa: F401
import not_dot_net.backend.workflow_service  # noqa: F401
import not_dot_net.frontend.audit_log  # noqa: F401
import not_dot_net.frontend.directory  # noqa: F401


async def _create_user(email, role):
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _setup():
    await seed_admin_permissions()
    cfg = await roles_config.get()
    cfg.roles["readonly"] = RoleDefinition(label="Read Only", permissions=[])
    cfg.roles["booker"] = RoleDefinition(
        label="Booker", permissions=["manage_bookings"]
    )
    cfg.roles["workflow_user"] = RoleDefinition(
        label="Workflow User", permissions=["create_workflows"]
    )
    cfg.roles["director"] = RoleDefinition(
        label="Director", permissions=["approve_workflows"]
    )
    await roles_config.set(cfg)


async def test_admin_can_do_everything():
    await _setup()
    admin = await _create_user("admin@test.com", "admin")
    all_perms = list(get_permissions().keys())
    for perm in all_perms:
        assert await has_permissions(admin, perm), f"admin should have {perm}"


async def test_readonly_cannot_do_anything():
    await _setup()
    user = await _create_user("ro@test.com", "readonly")
    all_perms = list(get_permissions().keys())
    for perm in all_perms:
        assert not await has_permissions(user, perm), f"readonly should not have {perm}"


async def test_booker_can_manage_resources():
    await _setup()
    booker = await _create_user("booker@test.com", "booker")
    r = await create_resource("Test PC", "desktop", actor=booker)
    assert r.name == "Test PC"
    await update_resource(r.id, actor=booker, name="Updated PC")
    await delete_resource(r.id, actor=booker)


async def test_booker_cannot_create_workflows():
    await _setup()
    booker = await _create_user("booker@test.com", "booker")
    with pytest.raises(PermissionError):
        await create_request(
            "vpn_access", booker.id,
            data={"target_name": "A", "target_email": "a@test.com"},
            actor=booker,
        )


async def test_workflow_user_cannot_manage_resources():
    await _setup()
    wf_user = await _create_user("wf@test.com", "workflow_user")
    with pytest.raises(PermissionError):
        await create_resource("PC", "desktop", actor=wf_user)


async def test_unknown_role_has_no_permissions():
    await _setup()
    user = await _create_user("ghost@test.com", "nonexistent_role")
    assert not await has_permissions(user, "manage_bookings")
    with pytest.raises(PermissionError):
        await check_permission(user, "manage_bookings")


async def test_readonly_user_can_create_booking_for_self():
    await _setup()
    user = await _create_user("selfbook@test.com", "readonly")
    resource = await create_resource("Self Service Laptop", "laptop")

    booking = await create_booking(
        resource_id=resource.id,
        user_id=user.id,
        start_date=date.today() + timedelta(days=1),
        end_date=date.today() + timedelta(days=2),
        actor=user,
    )

    assert booking.user_id == user.id
    assert booking.resource_id == resource.id


async def test_readonly_user_cannot_create_booking_for_another_user():
    await _setup()
    user = await _create_user("readonly-book@test.com", "readonly")
    other = await _create_user("target-book@test.com", "readonly")
    resource = await create_resource("Shared Laptop", "laptop")

    with pytest.raises(PermissionError):
        await create_booking(
            resource_id=resource.id,
            user_id=other.id,
            start_date=date.today() + timedelta(days=1),
            end_date=date.today() + timedelta(days=2),
            actor=user,
        )


async def test_booker_can_create_booking_for_another_user():
    await _setup()
    manager = await _create_user("manager-book@test.com", "booker")
    other = await _create_user("beneficiary-book@test.com", "readonly")
    resource = await create_resource("Managed Laptop", "laptop")

    booking = await create_booking(
        resource_id=resource.id,
        user_id=other.id,
        start_date=date.today() + timedelta(days=1),
        end_date=date.today() + timedelta(days=2),
        actor=manager,
    )

    assert booking.user_id == other.id


async def test_readonly_user_cannot_cancel_another_users_booking():
    await _setup()
    owner = await _create_user("owner-book@test.com", "readonly")
    other = await _create_user("other-book@test.com", "readonly")
    resource = await create_resource("Booked Resource", "desktop")
    booking = await create_booking(
        resource_id=resource.id,
        user_id=owner.id,
        start_date=date.today() + timedelta(days=1),
        end_date=date.today() + timedelta(days=2),
        actor=owner,
    )

    with pytest.raises(PermissionError):
        await cancel_booking(booking.id, actor=other)


async def test_booker_can_cancel_another_users_booking():
    await _setup()
    owner = await _create_user("owner-book2@test.com", "readonly")
    manager = await _create_user("manager-book2@test.com", "booker")
    resource = await create_resource("Managed Resource", "desktop")
    booking = await create_booking(
        resource_id=resource.id,
        user_id=owner.id,
        start_date=date.today() + timedelta(days=1),
        end_date=date.today() + timedelta(days=2),
        actor=owner,
    )

    await cancel_booking(booking.id, actor=manager)


async def test_workflow_user_cannot_approve_director_step():
    await _setup()
    requester = await _create_user("requester@test.com", "workflow_user")
    wrong_actor = await _create_user("wrong-approver@test.com", "workflow_user")
    req = await create_request(
        "vpn_access",
        requester.id,
        data={"target_name": "A", "target_email": "a@test.com"},
        actor=requester,
    )
    req = await submit_step(req.id, requester.id, "submit", data={}, actor_user=requester)

    with pytest.raises(PermissionError):
        await submit_step(req.id, wrong_actor.id, "approve", data={}, actor_user=wrong_actor)


async def test_director_can_approve_workflow_step():
    await _setup()
    requester = await _create_user("requester2@test.com", "workflow_user")
    director = await _create_user("director@test.com", "director")
    req = await create_request(
        "vpn_access",
        requester.id,
        data={"target_name": "A", "target_email": "a@test.com"},
        actor=requester,
    )
    req = await submit_step(req.id, requester.id, "submit", data={}, actor_user=requester)
    req = await submit_step(req.id, director.id, "approve", data={}, actor_user=director)

    assert req.status == "completed"
