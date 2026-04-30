import pytest
import uuid
from contextlib import asynccontextmanager

from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.workflow_service import create_request, submit_step
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
    from not_dot_net.backend.workflow_service import can_view_request
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
    from not_dot_net.backend.workflow_service import can_view_request
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
    from not_dot_net.backend.workflow_service import can_view_request
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


async def test_can_view_request_current_step_assignee():
    """A user assigned to the current workflow step can view the request detail."""
    from not_dot_net.backend.workflow_service import can_view_request
    await _setup_roles()
    staff = await _create_user(email="creator4@test.com", role="staff")
    director = await _create_user(email="director4@test.com", role="director")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    assert req.current_step == "approval"

    assert await can_view_request(director, req) is True


async def test_cannot_view_request_other_staff_on_approval_step():
    """A same-role user who is neither creator nor current assignee cannot IDOR the detail page."""
    from not_dot_net.backend.workflow_service import can_view_request
    await _setup_roles()
    creator = await _create_user(email="creator5@test.com", role="staff")
    other_staff = await _create_user(email="other5@test.com", role="staff")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=creator.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    req = await submit_step(req.id, creator.id, "submit", data={}, actor_user=creator)
    assert req.current_step == "approval"

    assert await can_view_request(other_staff, req) is False


async def test_target_person_can_view_their_onboarding_request():
    """The target person can view the token-owned onboarding step when their email matches."""
    from not_dot_net.backend.workflow_service import can_view_request
    await _setup_roles()
    initiator = await _create_user(email="creator6@test.com", role="staff")
    target = await _create_user(email="newcomer6@test.com", role="member")
    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": target.email, "status": "PhD"},
        actor=initiator,
    )
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)
    assert req.current_step == "newcomer_info"

    assert await can_view_request(target, req) is True


async def test_non_target_person_cannot_view_onboarding_target_step():
    """A different logged-in user cannot view another target person's onboarding request."""
    from not_dot_net.backend.workflow_service import can_view_request
    await _setup_roles()
    initiator = await _create_user(email="creator7@test.com", role="staff")
    other = await _create_user(email="other7@test.com", role="member")
    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "newcomer7@test.com", "status": "PhD"},
        actor=initiator,
    )
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)
    assert req.current_step == "newcomer_info"

    assert await can_view_request(other, req) is False
