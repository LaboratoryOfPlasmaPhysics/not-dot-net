"""I10 + S4 — the verification code's attempt counter and single-use property.

I10: verify_code read code_attempts with no row lock, so N parallel requests all
passed the MAX_ATTEMPTS gate before any of them incremented it. submit_step
takes with_for_update for exactly this reason.

S4: on a correct code the attempt counter reset but the hash was kept, so the
code stayed usable for its whole validity window. Anyone who saw it once — a
shared mailbox, a forwarded email, a glance at the screen — could re-open the
gated form. A one-time code should be exactly that.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.verification import MAX_ATTEMPTS, verify_code
from not_dot_net.backend.workflow_models import RequestStatus, WorkflowRequest


async def _request_with_code(code: str = "123456") -> uuid.UUID:
    from not_dot_net.backend.verification import _hash_code

    async with session_scope() as session:
        req = WorkflowRequest(
            type="onboarding", status=RequestStatus.IN_PROGRESS,
            current_step="newcomer_info", target_email="t@example.com",
            verification_code_hash=_hash_code(code),
            code_expires_at=(
                datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
            ),
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
        return req.id


async def test_verify_code_locks_the_request_row(monkeypatch):
    calls: list[tuple[object, dict]] = []
    original = AsyncSession.get

    async def spy(self, entity, ident, **kwargs):
        calls.append((entity, kwargs))
        return await original(self, entity, ident, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", spy)
    request_id = await _request_with_code()
    calls.clear()
    await verify_code(request_id, "000000")

    request_gets = [kw for entity, kw in calls if entity is WorkflowRequest]
    assert request_gets, "verify_code should fetch the request via session.get"
    assert any(kw.get("with_for_update") for kw in request_gets), (
        "verify_code read code_attempts without the lock submit_step uses"
    )


async def test_correct_code_cannot_be_replayed():
    request_id = await _request_with_code("654321")

    assert await verify_code(request_id, "654321") is True
    assert await verify_code(request_id, "654321") is False, (
        "the code was still accepted after being used once"
    )


async def test_successful_use_clears_the_stored_hash():
    request_id = await _request_with_code("111222")
    await verify_code(request_id, "111222")

    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)
        assert req.verification_code_hash is None
        assert req.code_expires_at is None
        assert req.code_attempts == 0


async def test_wrong_codes_still_count_towards_the_lockout():
    request_id = await _request_with_code("999888")

    for _ in range(MAX_ATTEMPTS):
        assert await verify_code(request_id, "000000") is False

    with pytest.raises(PermissionError):
        await verify_code(request_id, "999888")


async def test_expired_code_is_rejected():
    from not_dot_net.backend.verification import _hash_code

    async with session_scope() as session:
        req = WorkflowRequest(
            type="onboarding", status=RequestStatus.IN_PROGRESS,
            current_step="newcomer_info", target_email="t2@example.com",
            verification_code_hash=_hash_code("777777"),
            code_expires_at=(
                datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
            ),
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
        request_id = req.id

    assert await verify_code(request_id, "777777") is False
