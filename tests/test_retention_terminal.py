"""I13 — encrypted personal documents on non-completed requests were never purged.

Retention was set only on COMPLETED, so a cancelled or rejected request kept its
encrypted files (and their wrapped DEKs) indefinitely. The retention purge only
covers rows with `retained_until` set, so nothing ever collected them.
"""
import uuid

import pytest

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.encrypted_storage import EncryptedFile, store_encrypted
from not_dot_net.backend.workflow_models import (
    RequestStatus, WorkflowFile, WorkflowRequest,
)


async def _make_user(email: str) -> User:
    async with session_scope() as session:
        user = User(email=email, hashed_password="x", is_active=True, role="")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _request_with_encrypted_file(creator_id, status=RequestStatus.IN_PROGRESS):
    enc = await store_encrypted(b"passport scan", "passport.pdf", "application/pdf", creator_id)
    enc_id = enc.id
    async with session_scope() as session:
        req = WorkflowRequest(
            type="onboarding", status=status, current_step="collect",
            created_by=creator_id, target_email="newbie@example.com",
        )
        session.add(req)
        await session.flush()
        session.add(WorkflowFile(
            request_id=req.id, step_key="collect", field_name="id_document",
            filename="passport.pdf", storage_path="",
            encrypted_file_id=enc_id,
        ))
        await session.commit()
        await session.refresh(req)
        return req.id, enc_id


async def _retained_until(enc_id):
    async with session_scope() as session:
        return (await session.get(EncryptedFile, enc_id)).retained_until


async def test_cancelling_a_request_schedules_its_files_for_deletion():
    from not_dot_net.backend.workflow_service import cancel_request

    creator = await _make_user("creator-cancel@example.com")
    request_id, enc_id = await _request_with_encrypted_file(creator.id)
    assert await _retained_until(enc_id) is None

    await cancel_request(request_id, creator.id, actor_user=creator)

    assert await _retained_until(enc_id) is not None, (
        "cancelled request kept its encrypted personal documents forever"
    )


async def test_completed_keeps_the_longer_retention():
    from not_dot_net.backend.workflow_service import _RETENTION_DAYS, schedule_file_retention

    creator = await _make_user("creator-done@example.com")
    request_id, enc_id = await _request_with_encrypted_file(creator.id)

    await schedule_file_retention(request_id, RequestStatus.COMPLETED)
    completed_deadline = await _retained_until(enc_id)
    assert completed_deadline is not None
    assert _RETENTION_DAYS[RequestStatus.COMPLETED] == 365


async def test_rejected_gets_a_shorter_retention_than_completed():
    """A request that never completed has no reason to hold personal data
    for as long as one that did."""
    from not_dot_net.backend.workflow_service import _RETENTION_DAYS

    assert (
        _RETENTION_DAYS[RequestStatus.REJECTED]
        < _RETENTION_DAYS[RequestStatus.COMPLETED]
    )
    assert (
        _RETENTION_DAYS[RequestStatus.CANCELLED]
        < _RETENTION_DAYS[RequestStatus.COMPLETED]
    )


async def test_in_progress_requests_are_not_scheduled():
    from not_dot_net.backend.workflow_service import schedule_file_retention

    creator = await _make_user("creator-open@example.com")
    request_id, enc_id = await _request_with_encrypted_file(creator.id)

    await schedule_file_retention(request_id, RequestStatus.IN_PROGRESS)
    assert await _retained_until(enc_id) is None


async def test_retention_failure_does_not_propagate():
    """The transition is already committed; a retention failure must not
    surface as an error to the caller."""
    from not_dot_net.backend import workflow_service

    creator = await _make_user("creator-boom@example.com")
    request_id, _ = await _request_with_encrypted_file(creator.id)

    async def boom(*a, **k):
        raise RuntimeError("storage down")

    original = workflow_service.session_scope
    workflow_service.session_scope = boom
    try:
        await workflow_service.schedule_file_retention(
            request_id, RequestStatus.CANCELLED
        )
    finally:
        workflow_service.session_scope = original
