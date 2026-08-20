"""B3/U1 — the token page's submit and save-draft handlers had no error handling.

An external user whose token expired while filling the form, or who
double-clicked Submit, got an unhandled NiceGUI exception: no feedback, and the
data they had typed was gone. workflow_detail.py has always caught and notified;
this path never did.
"""
import uuid

import pytest
from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import RequestStatus, WorkflowRequest


async def _token_request() -> tuple[uuid.UUID, str]:
    token = str(uuid.uuid4())
    async with session_scope() as session:
        req = WorkflowRequest(
            type="onboarding", status=RequestStatus.IN_PROGRESS,
            current_step="newcomer_info", target_email="t@example.com",
            token=token,
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
        return req.id, token


async def test_submit_failure_is_reported_not_raised(monkeypatch):
    """A stale token must produce a message, not an unhandled exception."""
    from not_dot_net.frontend import workflow_token as wt

    async def stale(*a, **k):
        raise PermissionError("Invalid or expired token")

    monkeypatch.setattr(wt, "submit_step", stale)

    notified = []
    monkeypatch.setattr(wt.ui, "notify", lambda msg, **k: notified.append(msg))

    request_id, token = await _token_request()
    handler = wt._make_submit_handler(request_id, token, on_success=lambda: None)

    await handler({})  # must not raise
    assert notified, "submit failure produced no user-visible message"


async def test_save_draft_failure_is_reported_not_raised(monkeypatch):
    from not_dot_net.frontend import workflow_token as wt

    async def stale(*a, **k):
        raise PermissionError("Invalid or expired token")

    monkeypatch.setattr(wt, "save_draft", stale)

    notified = []
    monkeypatch.setattr(wt.ui, "notify", lambda msg, **k: notified.append(msg))

    request_id, token = await _token_request()
    handler = wt._make_save_draft_handler(request_id, token)

    await handler({})
    assert notified, "save-draft failure produced no user-visible message"


async def test_successful_submit_still_runs_the_success_path(monkeypatch):
    from not_dot_net.frontend import workflow_token as wt

    async def ok(*a, **k):
        return None

    monkeypatch.setattr(wt, "submit_step", ok)
    monkeypatch.setattr(wt.ui, "notify", lambda msg, **k: None)

    ran = []
    request_id, token = await _token_request()
    handler = wt._make_submit_handler(request_id, token, on_success=lambda: ran.append(True))

    await handler({})
    assert ran == [True]
