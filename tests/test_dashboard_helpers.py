import pytest
import uuid
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager

from not_dot_net.config import DashboardConfig
from not_dot_net.backend.workflow_service import (
    create_request,
    submit_step,
    list_events,
    get_actionable_count,
    compute_step_age_days,
)
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.db import User, get_async_session


async def test_dashboard_config_defaults():
    cfg = DashboardConfig()
    assert cfg.urgency_fresh_days == 2
    assert cfg.urgency_aging_days == 7


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
    cfg.roles["member"] = RoleDefinition(label="Member", permissions=[])
    await roles_config.set(cfg)


async def test_compute_step_age_days():
    await _setup_roles()
    staff = await _create_user(email="age_staff@test.com", role="staff")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "X", "target_email": "x@test.com"},
    )
    await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    events = await list_events(req.id)
    age = compute_step_age_days(events, "approval")
    assert isinstance(age, int)
    assert age >= 0


async def test_get_actionable_count():
    await _setup_roles()
    staff = await _create_user(email="staff2@test.com", role="staff")
    director = await _create_user(email="director2@test.com", role="director")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    count = await get_actionable_count(director)
    assert count == 1


async def test_get_actionable_count_zero():
    await _setup_roles()
    staff = await _create_user(email="staff3@test.com", role="staff")
    member_user = await _create_user(email="member3@test.com", role="member")
    cfg = await roles_config.get()
    cfg.roles["member"] = RoleDefinition(label="Member", permissions=[])
    await roles_config.set(cfg)
    await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    count = await get_actionable_count(member_user)
    assert count == 0


async def test_get_actionable_count_excludes_completed_requests():
    await _setup_roles()
    staff = await _create_user(email="staff4@test.com", role="staff")
    director = await _create_user(email="director4@test.com", role="director")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    await submit_step(req.id, director.id, "approve", data={}, actor_user=director)

    count = await get_actionable_count(director)

    assert count == 0


async def test_get_actionable_count_target_person_only_counts_matching_email():
    await _setup_roles()
    initiator = await _create_user(email="staff5@test.com", role="staff")
    target = await _create_user(email="target5@test.com", role="member")
    other = await _create_user(email="other5@test.com", role="member")
    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": target.email, "status": "PhD"},
        actor=initiator,
    )
    await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)

    assert await get_actionable_count(target) == 1
    assert await get_actionable_count(other) == 0
