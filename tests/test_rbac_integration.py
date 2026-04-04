"""End-to-end RBAC integration test — roles, permissions, enforcement."""

import uuid
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
)
from not_dot_net.backend.workflow_service import create_request


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
