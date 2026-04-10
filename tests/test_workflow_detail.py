import pytest
import uuid
from contextlib import asynccontextmanager

from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.workflow_service import create_request, get_request_by_id
from not_dot_net.backend.roles import RoleDefinition, roles_config


async def _create_user(email="staff@test.com", role="staff") -> User:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password="x",
            role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=["manage_bookings", "manage_roles", "manage_settings",
                     "create_workflows", "approve_workflows", "view_audit_log", "manage_users"],
    )
    cfg.roles["staff"] = RoleDefinition(
        label="Staff",
        permissions=["create_workflows"],
    )
    cfg.roles["director"] = RoleDefinition(
        label="Director",
        permissions=["create_workflows", "approve_workflows"],
    )
    await roles_config.set(cfg)


async def test_can_view_request_creator():
    """Request creator can view their own request."""
    from not_dot_net.backend.workflow_file_routes import can_view_request
    await _setup_roles()
    staff = await _create_user(email="creator@test.com", role="staff")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    assert await can_view_request(staff, req) is True


async def test_can_view_request_admin():
    """Admin with view_audit_log can view any request."""
    from not_dot_net.backend.workflow_file_routes import can_view_request
    await _setup_roles()
    staff = await _create_user(email="creator2@test.com", role="staff")
    admin = await _create_user(email="admin2@test.com", role="admin")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    assert await can_view_request(admin, req) is True


async def test_cannot_view_request_unrelated():
    """Unrelated user without permissions cannot view."""
    from not_dot_net.backend.workflow_file_routes import can_view_request
    await _setup_roles()
    staff = await _create_user(email="creator3@test.com", role="staff")
    other = await _create_user(email="other3@test.com", role="member")
    cfg = await roles_config.get()
    cfg.roles["member"] = RoleDefinition(label="Member", permissions=[])
    await roles_config.set(cfg)
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    assert await can_view_request(other, req) is False
