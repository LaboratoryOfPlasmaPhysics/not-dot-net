import pytest
import uuid
from unittest.mock import patch, AsyncMock
from not_dot_net.backend.workflow_service import create_request, submit_step
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.db import User, get_async_session
from not_dot_net.config import OrgConfig, org_config
from contextlib import asynccontextmanager


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
    cfg.roles["admin"] = RoleDefinition(
        label="Admin", permissions=["manage_roles", "manage_settings", "create_workflows", "approve_workflows"],
    )
    await roles_config.set(cfg)


async def test_submit_step_fires_notifications():
    """After submitting the request step of vpn_access, directors should be notified."""
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    director = await _create_user(email="director@test.com", role="director")

    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )

    with patch("not_dot_net.backend.workflow_service.notify", new_callable=AsyncMock) as mock_notify:
        mock_notify.return_value = ["director@test.com"]
        await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
        mock_notify.assert_called_once()
        assert mock_notify.call_args.kwargs["event"] == "submit"


async def test_approve_fires_notifications():
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    director = await _create_user(email="director@test.com", role="director")

    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )

    # Submit first step (mock notifications)
    with patch("not_dot_net.backend.workflow_service.notify", new_callable=AsyncMock) as mock_notify:
        mock_notify.return_value = []
        req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)

    # Approve (check notifications fire)
    with patch("not_dot_net.backend.workflow_service.notify", new_callable=AsyncMock) as mock_notify:
        mock_notify.return_value = []
        await submit_step(req.id, director.id, "approve", data={}, actor_user=director)
        mock_notify.assert_called_once()


async def test_onboarding_submit_sends_target_token_link_email():
    await _setup_roles()
    await org_config.set(OrgConfig(base_url="https://intranet.example.test/"))
    staff = await _create_user(email="staff-token@test.com", role="staff")

    req = await create_request(
        workflow_type="onboarding",
        created_by=staff.id,
        data={"contact_email": "newcomer-token@test.com", "status": "PhD", "employer": "CNRS"},
        actor=staff,
    )

    sent_emails = []

    async def fake_send_mail(to, subject, body_html, mail_settings):
        sent_emails.append((to, subject, body_html))

    with patch("not_dot_net.backend.mail.send_mail", side_effect=fake_send_mail):
        req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)

    assert req.token is not None
    assert sent_emails == [
        (
            "newcomer-token@test.com",
            "Please complete your information for Onboarding",
            f'<p>Please complete your information by visiting the link below:</p><p><a href="https://intranet.example.test/workflow/token/{req.token}">https://intranet.example.test/workflow/token/{req.token}</a></p>',
        )
    ]


async def test_onboarding_request_corrections_sends_fresh_token_link_email():
    await _setup_roles()
    await org_config.set(OrgConfig(base_url="https://intranet.example.test/"))
    cfg = await roles_config.get()
    cfg.roles["admin"].permissions.append("access_personal_data")
    await roles_config.set(cfg)
    staff = await _create_user(email="staff-corrections@test.com", role="staff")
    admin = await _create_user(email="admin-corrections@test.com", role="admin")

    req = await create_request(
        workflow_type="onboarding",
        created_by=staff.id,
        data={"contact_email": "newcomer-corrections@test.com", "status": "PhD", "employer": "CNRS"},
        actor=staff,
    )

    with patch("not_dot_net.backend.mail.send_mail", new_callable=AsyncMock):
        req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
        req = await submit_step(
            req.id,
            actor_id=None,
            action="submit",
            data={"first_name": "Marie", "last_name": "Curie"},
            actor_token=req.token,
        )

    sent_emails = []

    async def fake_send_mail(to, subject, body_html, mail_settings):
        sent_emails.append((to, subject, body_html))

    with patch("not_dot_net.backend.mail.send_mail", side_effect=fake_send_mail):
        req = await submit_step(
            req.id,
            admin.id,
            "request_corrections",
            comment="Missing document",
            actor_user=admin,
        )

    assert req.token is not None
    assert len(sent_emails) == 1
    to, subject, body = sent_emails[0]
    assert to == "newcomer-corrections@test.com"
    assert subject == "Corrections needed for your Onboarding submission"
    assert f"https://intranet.example.test/workflow/token/{req.token}" in body
    assert "visit the link you received previously" not in body


async def test_onboarding_complete_notification_does_not_include_token_link():
    await _setup_roles()
    await org_config.set(OrgConfig(base_url="https://intranet.example.test/"))
    cfg = await roles_config.get()
    cfg.roles["admin"].permissions.append("access_personal_data")
    cfg.roles["admin"].permissions.append("manage_users")
    await roles_config.set(cfg)
    staff = await _create_user(email="staff-complete@test.com", role="staff")
    admin = await _create_user(email="admin-complete@test.com", role="admin")

    req = await create_request(
        workflow_type="onboarding",
        created_by=staff.id,
        data={"contact_email": "newcomer-complete@test.com", "status": "PhD", "employer": "CNRS"},
        actor=staff,
    )

    with patch("not_dot_net.backend.mail.send_mail", new_callable=AsyncMock):
        req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
        req = await submit_step(
            req.id,
            actor_id=None,
            action="submit",
            data={"first_name": "Marie", "last_name": "Curie"},
            actor_token=req.token,
        )
        req = await submit_step(req.id, admin.id, "approve", data={}, actor_user=admin)

    sent_emails = []

    async def fake_send_mail(to, subject, body_html, mail_settings):
        sent_emails.append((to, subject, body_html))

    with patch("not_dot_net.backend.mail.send_mail", side_effect=fake_send_mail):
        req = await submit_step(req.id, admin.id, "complete", data={"notes": "created"}, actor_user=admin)

    assert req.status == "completed"
    assert sent_emails
    assert all("/workflow/token/" not in body for _, _, body in sent_emails)
