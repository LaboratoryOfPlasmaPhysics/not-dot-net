"""Reproducers for the 2026-07-02 whole-branch review findings:
cancel_request row lock + basic guards, save_draft token stability,
resend_notification row lock, and validate_upload unit coverage."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from not_dot_net.backend.workflow_models import WorkflowRequest, RequestStatus
from not_dot_net.backend.workflow_service import (
    cancel_request,
    create_request,
    resend_notification,
    submit_step,
)
from not_dot_net.backend.workflow_uploads import validate_upload
from tests.test_workflow_service import _create_user, _setup_roles
from tests.test_token_security import _start_onboarding_to_newcomer


# --- cancel_request: row lock + guards (previously untested) ---


async def test_cancel_request_locks_row(monkeypatch):
    """Terminal-status guard must be checked under FOR UPDATE, like submit_step
    and save_draft, or a cancel can race a concurrent final approve."""
    await _setup_roles()
    creator = await _create_user()
    req = await create_request(
        workflow_type="onboarding", created_by=creator.id,
        data={"contact_email": "bob@test.com"},
    )

    calls: list[dict] = []
    original_get = AsyncSession.get

    async def spy_get(self, entity, ident, **kwargs):
        if entity is WorkflowRequest:
            calls.append(kwargs)
        return await original_get(self, entity, ident, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", spy_get)
    await cancel_request(req.id, creator.id)
    assert any(kw.get("with_for_update") for kw in calls)


async def test_cancel_request_creator_only():
    await _setup_roles()
    creator = await _create_user()
    stranger = await _create_user(email="stranger@test.com")
    req = await create_request(
        workflow_type="onboarding", created_by=creator.id,
        data={"contact_email": "bob@test.com"},
    )
    with pytest.raises(PermissionError):
        await cancel_request(req.id, stranger.id)


async def test_cancel_request_rejects_terminal_request():
    await _setup_roles()
    creator = await _create_user()
    req = await create_request(
        workflow_type="onboarding", created_by=creator.id,
        data={"contact_email": "bob@test.com"},
    )
    await cancel_request(req.id, creator.id)
    with pytest.raises(ValueError, match="in-progress"):
        await cancel_request(req.id, creator.id)


async def test_cancel_request_clears_token():
    req, token = await _start_onboarding_to_newcomer()
    assert token
    cancelled = await cancel_request(req.id, req.created_by)
    assert cancelled.status == RequestStatus.CANCELLED
    assert cancelled.token is None
    assert cancelled.token_expires_at is None


# --- submit_step(save_draft) must not rotate the target_person token ---


async def test_submit_step_save_draft_keeps_token():
    """Latent: the token-generate block ran for save_draft (next step == same
    target_person step), silently replacing the URL the target is using."""
    req, token = await _start_onboarding_to_newcomer()
    updated = await submit_step(
        req.id, actor_id=None, action="save_draft",
        data={"phone": "+33 1 23 45"}, actor_token=token,
    )
    assert updated.token == token


# --- resend_notification: row lock ---


async def test_resend_notification_locks_row(monkeypatch):
    req, old_token = await _start_onboarding_to_newcomer()
    admin = await _create_user(email="resender@test.com", role="admin")

    calls: list[dict] = []
    original_get = AsyncSession.get

    async def spy_get(self, entity, ident, **kwargs):
        if entity is WorkflowRequest:
            calls.append(kwargs)
        return await original_get(self, entity, ident, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", spy_get)
    updated = await resend_notification(req.id, actor_user=admin)
    assert any(kw.get("with_for_update") for kw in calls)
    assert updated.token and updated.token != old_token


# --- validate_upload: direct unit coverage (was only tested indirectly) ---


@pytest.mark.parametrize(
    "content,filename,expected_error",
    [
        (b"x" * (2 * 1024 * 1024), "big.pdf", "too large"),
        (b"%PDF-1.4 data", "script.exe", "not allowed"),
        (b"%PDF-1.4 data", "fake.png", "does not match"),
        (b"%PDF-1.4 data", "ok.pdf", None),
        (b"\x89PNG\r\n\x1a\nimg", "photo.png", None),
    ],
)
def test_validate_upload(content, filename, expected_error):
    error = validate_upload(content, filename, "application/octet-stream", 1)
    if expected_error is None:
        assert error is None
    else:
        assert error is not None and expected_error in error
