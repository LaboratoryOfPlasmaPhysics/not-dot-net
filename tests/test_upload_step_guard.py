"""I11 — the token page validated the request, then wrote the file in a second
transaction with no lock in between.

A submit_step landing in that window advances the request and rotates its token,
but the upload still attached itself to the step the page was showing — so a
file arrived against an already-reviewed step, displacing its "current" version
after the fact.
"""
import uuid

import pytest

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import (
    RequestStatus, WorkflowFile, WorkflowRequest,
)
from not_dot_net.backend.workflow_service import persist_workflow_upload


async def _request(step: str = "newcomer_info", **kwargs) -> WorkflowRequest:
    async with session_scope() as session:
        req = WorkflowRequest(
            type="onboarding", status=RequestStatus.IN_PROGRESS,
            current_step=step, target_email="t@example.com",
            token=str(uuid.uuid4()), **kwargs,
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
        return req


async def _files(request_id) -> list[WorkflowFile]:
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(
            select(WorkflowFile).where(WorkflowFile.request_id == request_id)
        )).scalars().all())


async def test_upload_is_refused_when_the_step_moved_on():
    req = await _request(step="newcomer_info")

    # The request advances between the page's check and the write.
    async with session_scope() as session:
        stored = await session.get(WorkflowRequest, req.id)
        stored.current_step = "review"
        await session.commit()

    with pytest.raises(PermissionError):
        await persist_workflow_upload(
            request_id=req.id, step_key="newcomer_info", field_name="id_document",
            content=b"%PDF-1.4 x", filename="id.pdf", content_type="application/pdf",
            encrypted=False, uploaded_by=None, expected_step_key="newcomer_info",
        )
    assert await _files(req.id) == []


async def test_upload_is_refused_once_the_request_is_terminal():
    req = await _request()
    async with session_scope() as session:
        stored = await session.get(WorkflowRequest, req.id)
        stored.status = RequestStatus.COMPLETED
        await session.commit()

    with pytest.raises(PermissionError):
        await persist_workflow_upload(
            request_id=req.id, step_key="newcomer_info", field_name="id_document",
            content=b"%PDF-1.4 x", filename="id.pdf", content_type="application/pdf",
            encrypted=False, uploaded_by=None, expected_step_key="newcomer_info",
        )
    assert await _files(req.id) == []


async def test_upload_proceeds_when_the_step_still_matches():
    req = await _request()
    saved = await persist_workflow_upload(
        request_id=req.id, step_key="newcomer_info", field_name="id_document",
        content=b"%PDF-1.4 x", filename="id.pdf", content_type="application/pdf",
        encrypted=False, uploaded_by=None, expected_step_key="newcomer_info",
    )
    assert saved.id is not None
    assert len(await _files(req.id)) == 1


async def test_guard_is_opt_in_so_existing_callers_are_unchanged():
    req = await _request(step="review")
    saved = await persist_workflow_upload(
        request_id=req.id, step_key="newcomer_info", field_name="id_document",
        content=b"%PDF-1.4 x", filename="id.pdf", content_type="application/pdf",
        encrypted=False, uploaded_by=None,
    )
    assert saved.id is not None
