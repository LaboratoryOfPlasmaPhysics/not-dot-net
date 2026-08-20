"""I9 — resend rotated the token before the email was enqueued, then swallowed
any failure. The old link died instantly, the new one never reached anyone, and
the admin was told "notification resent".
"""
import uuid

import pytest

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.workflow_models import RequestStatus, WorkflowRequest


async def _make_admin(email: str) -> User:
    async with session_scope() as session:
        user = User(
            email=email, hashed_password="x", is_active=True,
            is_superuser=True, role="",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _token_request(step: str = "newcomer_info") -> uuid.UUID:
    async with session_scope() as session:
        req = WorkflowRequest(
            type="onboarding", status=RequestStatus.IN_PROGRESS,
            current_step=step, target_email="newbie@example.com",
            token=str(uuid.uuid4()),
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
        return req.id


async def _token_of(request_id) -> str | None:
    async with session_scope() as session:
        return (await session.get(WorkflowRequest, request_id)).token


async def test_resend_reports_a_send_failure_instead_of_claiming_success(monkeypatch):
    from not_dot_net.backend import workflow_service as ws

    admin = await _make_admin("resend-admin@example.com")
    request_id = await _token_request()
    original_token = await _token_of(request_id)

    async def failing_send(*a, **k):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(ws, "_send_token_link", failing_send)

    with pytest.raises(Exception) as caught:
        await ws.resend_notification(request_id, actor_user=admin)
    assert "outbox" in str(caught.value).lower() or isinstance(caught.value, RuntimeError)

    # The token did rotate, so the admin must be told — otherwise the target is
    # left holding a dead link and nobody knows.
    assert await _token_of(request_id) != original_token


async def test_successful_resend_rotates_the_token(monkeypatch):
    from not_dot_net.backend import workflow_service as ws

    admin = await _make_admin("resend-ok@example.com")
    request_id = await _token_request()
    original_token = await _token_of(request_id)

    sent = []

    async def ok_send(req, wf):
        sent.append(req.id)

    monkeypatch.setattr(ws, "_send_token_link", ok_send)

    await ws.resend_notification(request_id, actor_user=admin)

    assert sent == [request_id]
    assert await _token_of(request_id) != original_token


class TestResendPermissionPredicate:
    """D7 — the same three-permission OR-check lived in the UI and the service.

    Drift showed or hid the button independently of what was enforced: a role
    granted access_personal_data but not the others would see no button while
    the service would have let them through, or worse the reverse.
    """

    async def test_predicate_matches_what_the_service_enforces(self):
        from not_dot_net.backend.workflow_service import can_resend_notification

        for permission in ("approve_workflows", "access_personal_data", "manage_users"):
            user = await _user_with_permission(permission)
            assert await can_resend_notification(user) is True, permission

    async def test_predicate_rejects_a_user_with_none_of_them(self):
        from not_dot_net.backend.workflow_service import can_resend_notification

        user = await _user_with_permission("view_directory")
        assert await can_resend_notification(user) is False

    async def test_service_and_predicate_agree_on_refusal(self):
        """The service must still refuse, not just the button disappear."""
        from not_dot_net.backend.workflow_service import (
            can_resend_notification,
            resend_notification,
        )

        user = await _user_with_permission("view_directory")
        assert await can_resend_notification(user) is False

        request_id = await _token_request()
        with pytest.raises(PermissionError):
            await resend_notification(request_id, actor_user=user)


async def _user_with_permission(permission: str) -> User:
    """A user whose role grants exactly `permission`."""
    from not_dot_net.backend.roles import RoleDefinition, roles_config

    role_key = f"role_{permission}"
    cfg = await roles_config.get()
    cfg.roles[role_key] = RoleDefinition(label=role_key, permissions=[permission])
    await roles_config.set(cfg)

    async with session_scope() as session:
        user = User(
            email=f"{role_key}-{uuid.uuid4().hex[:6]}@example.com",
            hashed_password="x", is_active=True, role=role_key,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user
