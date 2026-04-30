import pytest
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from not_dot_net.backend.verification import (
    generate_verification_code,
    has_valid_code,
    verify_code,
    MAX_ATTEMPTS,
)
from not_dot_net.backend.workflow_service import create_request, submit_step
from not_dot_net.backend.workflow_models import WorkflowRequest
from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.roles import RoleDefinition, roles_config


async def _create_user(email="staff@test.com", role="staff") -> User:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=["create_workflows"])
    cfg.roles["director"] = RoleDefinition(label="Director", permissions=["create_workflows", "approve_workflows"])
    await roles_config.set(cfg)


async def _create_test_request():
    """Create an onboarding request and advance past step 1."""
    await _setup_roles()
    user = await _create_user()
    req = await create_request(
        workflow_type="onboarding",
        created_by=user.id,
        data={"person_name": "Test Person", "person_email": "newcomer@test.com"},
    )
    req = await submit_step(req.id, user.id, "submit", data={}, actor_user=user)
    return req


@pytest.mark.asyncio
async def test_generate_code_returns_6_digits():
    req = await _create_test_request()
    code = await generate_verification_code(req.id)
    assert len(code) == 6
    assert code.isdigit()


@pytest.mark.asyncio
async def test_verify_code_correct():
    req = await _create_test_request()
    code = await generate_verification_code(req.id)
    result = await verify_code(req.id, code)
    assert result is True


@pytest.mark.asyncio
async def test_verify_code_wrong():
    req = await _create_test_request()
    await generate_verification_code(req.id)
    result = await verify_code(req.id, "000000")
    assert result is False


@pytest.mark.asyncio
async def test_verify_code_rate_limited():
    req = await _create_test_request()
    await generate_verification_code(req.id)
    for _ in range(MAX_ATTEMPTS):
        await verify_code(req.id, "000000")
    with pytest.raises(PermissionError, match="Too many"):
        await verify_code(req.id, "000000")


@pytest.mark.asyncio
async def test_reuse_valid_code():
    req = await _create_test_request()
    code1 = await generate_verification_code(req.id)
    assert code1 is not None
    code2 = await generate_verification_code(req.id)
    assert code2 is None
    result = await verify_code(req.id, code1)
    assert result is True


@pytest.mark.asyncio
async def test_code_survives_verification():
    """After verification, code stays in DB so has_valid_code still returns True."""
    req = await _create_test_request()
    code = await generate_verification_code(req.id)
    assert await has_valid_code(req.id) is True
    await verify_code(req.id, code)
    assert await has_valid_code(req.id) is True
    regenerated = await generate_verification_code(req.id)
    assert regenerated is None


@pytest.mark.asyncio
async def test_expired_code_is_invalid():
    req = await _create_test_request()
    code = await generate_verification_code(req.id)

    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        db_req = await session.get(WorkflowRequest, req.id)
        db_req.code_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

    assert await has_valid_code(req.id) is False
    assert await verify_code(req.id, code) is False


@pytest.mark.asyncio
async def test_expired_code_can_be_regenerated():
    req = await _create_test_request()
    first_code = await generate_verification_code(req.id)
    assert first_code is not None

    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        db_req = await session.get(WorkflowRequest, req.id)
        db_req.code_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

    new_code = await generate_verification_code(req.id)

    assert new_code is not None
    assert len(new_code) == 6
    assert new_code.isdigit()


@pytest.mark.asyncio
async def test_failed_attempts_do_not_allow_regeneration_before_expiry():
    req = await _create_test_request()
    code = await generate_verification_code(req.id)
    assert code is not None

    assert await verify_code(req.id, "000000") is False

    assert await generate_verification_code(req.id) is None
    assert await verify_code(req.id, code) is True
