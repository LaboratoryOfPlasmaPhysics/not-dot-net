"""Tests for workflow token expiry."""

from not_dot_net.backend.permissions import SYSTEM_ACTOR
import pytest
import uuid
from datetime import datetime, timedelta, timezone

from not_dot_net.backend.workflow_service import (
    create_request,
    submit_step,
    get_request_by_token,
)
from not_dot_net.backend.workflow_models import WorkflowRequest
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.db import User, session_scope


async def _create_user(email="staff@test.com", role="staff") -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=["create_workflows"])
    cfg.roles["admin"] = RoleDefinition(
        label="Admin", permissions=["manage_roles", "manage_settings", "create_workflows", "approve_workflows"],
    )
    await roles_config.set(cfg)


async def test_valid_token_found():
    """A token within its expiry window should be found."""
    await _setup_roles()
    user = await _create_user()
    req = await create_request(
        workflow_type="onboarding",
        created_by=user.id,
        data={"person_name": "Test", "person_email": "test@ext.com",
              "role_status": "postdoc", "team": "Plasma Physics", "start_date": "2026-04-01"}, actor=SYSTEM_ACTOR
    )
    # Submit the request step to advance to newcomer_info (which generates a token)
    req = await submit_step(req.id, user.id, "submit", data={}, actor_user=user)
    assert req.token is not None

    found = await get_request_by_token(req.token)
    assert found is not None
    assert found.id == req.id


async def test_expired_token_not_found():
    """An expired token should not be found."""
    await _setup_roles()
    user = await _create_user()
    req = await create_request(
        workflow_type="onboarding",
        created_by=user.id,
        data={"person_name": "Test", "person_email": "test@ext.com",
              "role_status": "postdoc", "team": "Plasma Physics", "start_date": "2026-04-01"}, actor=SYSTEM_ACTOR
    )
    req = await submit_step(req.id, user.id, "submit", data={}, actor_user=user)
    assert req.token is not None

    # Manually expire the token
    async with session_scope() as session:
        db_req = await session.get(WorkflowRequest, req.id)
        db_req.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await session.commit()

    found = await get_request_by_token(req.token)
    assert found is None


async def test_nonexistent_token_not_found():
    assert await get_request_by_token("nonexistent-token") is None
