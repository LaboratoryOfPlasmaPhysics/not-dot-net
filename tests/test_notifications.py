import pytest
import uuid
from unittest.mock import AsyncMock, patch
from not_dot_net.backend.notifications import notify, resolve_recipients, render_email
from not_dot_net.config import (
    WorkflowConfig,
    WorkflowStepConfig,
    NotificationRuleConfig,
    FieldConfig,
)
from not_dot_net.backend.mail import MailConfig


# --- Fixtures ---

VPN_WORKFLOW = WorkflowConfig(
    label="VPN Access Request",
    start_role="staff",
    target_email_field="target_email",
    steps=[
        WorkflowStepConfig(key="request", type="form", assignee_role="staff", actions=["submit"]),
        WorkflowStepConfig(key="approval", type="approval", assignee_role="director", actions=["approve", "reject"]),
    ],
    notifications=[
        NotificationRuleConfig(event="submit", step="request", notify=["director"]),
        NotificationRuleConfig(event="approve", notify=["requester", "target_person"]),
        NotificationRuleConfig(event="reject", notify=["requester"]),
    ],
)


class FakeRequest:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", uuid.uuid4())
        self.type = kwargs.get("type", "vpn_access")
        self.current_step = kwargs.get("current_step", "request")
        self.status = kwargs.get("status", "in_progress")
        self.data = kwargs.get("data", {})
        self.created_by = kwargs.get("created_by", uuid.uuid4())
        self.target_email = kwargs.get("target_email", "target@test.com")
        self.token = kwargs.get("token", None)


class FakeUser:
    def __init__(self, email, role="director", id=None):
        self.email = email
        self.role = role
        self.id = id or uuid.uuid4()
        self.is_active = True


# --- Tests: rule matching ---

def test_matching_rules_by_event_and_step():
    from not_dot_net.backend.notifications import _matching_rules
    rules = _matching_rules(VPN_WORKFLOW, "submit", "request")
    assert len(rules) == 1
    assert "director" in rules[0].notify


def test_matching_rules_event_only():
    from not_dot_net.backend.notifications import _matching_rules
    rules = _matching_rules(VPN_WORKFLOW, "approve", "approval")
    assert len(rules) == 1
    assert "requester" in rules[0].notify


def test_matching_rules_no_match():
    from not_dot_net.backend.notifications import _matching_rules
    rules = _matching_rules(VPN_WORKFLOW, "save_draft", "request")
    assert len(rules) == 0


# --- Tests: recipient resolution ---

async def test_resolve_requester():
    requester_id = uuid.uuid4()
    req = FakeRequest(created_by=requester_id)

    async def mock_get_email(user_id):
        return "requester@test.com"

    emails = await resolve_recipients(
        ["requester"], req, get_user_email=mock_get_email, get_users_by_role=AsyncMock(return_value=[]),
    )
    assert "requester@test.com" in emails


async def test_resolve_target_person():
    req = FakeRequest(target_email="newcomer@test.com")

    emails = await resolve_recipients(
        ["target_person"], req, get_user_email=AsyncMock(), get_users_by_role=AsyncMock(return_value=[]),
    )
    assert "newcomer@test.com" in emails


async def test_resolve_role():
    req = FakeRequest()

    async def mock_get_by_role(role_str):
        return [FakeUser("dir1@test.com"), FakeUser("dir2@test.com")]

    emails = await resolve_recipients(
        ["director"], req, get_user_email=AsyncMock(), get_users_by_role=mock_get_by_role,
    )
    assert "dir1@test.com" in emails
    assert "dir2@test.com" in emails


async def test_resolve_deduplicates_recipients():
    requester_id = uuid.uuid4()
    req = FakeRequest(created_by=requester_id, target_email="same@test.com")

    async def mock_get_email(user_id):
        return "same@test.com"

    emails = await resolve_recipients(
        ["requester", "target_person", "director"],
        req,
        get_user_email=mock_get_email,
        get_users_by_role=AsyncMock(return_value=[FakeUser("same@test.com")]),
    )

    assert emails == ["same@test.com"]


async def test_resolve_permission_target():
    req = FakeRequest()

    emails = await resolve_recipients(
        ["permission:access_personal_data"],
        req,
        get_user_email=AsyncMock(),
        get_users_by_role=AsyncMock(return_value=[]),
        get_users_by_permission=AsyncMock(return_value=[
            FakeUser("admin1@test.com"),
            FakeUser("admin2@test.com"),
        ]),
    )

    assert set(emails) == {"admin1@test.com", "admin2@test.com"}


async def test_resolve_permission_target_without_handler_does_not_fall_back_to_role():
    req = FakeRequest()
    get_users_by_role = AsyncMock(return_value=[FakeUser("wrong@test.com")])

    emails = await resolve_recipients(
        ["permission:access_personal_data"],
        req,
        get_user_email=AsyncMock(),
        get_users_by_role=get_users_by_role,
        get_users_by_permission=None,
    )

    assert emails == []
    get_users_by_role.assert_not_awaited()


# --- Tests: email rendering ---

def test_render_submit_email():
    subject, body = render_email("submit", "VPN Access Request", step_label="Request")
    assert "VPN Access Request" in subject
    assert "VPN Access Request" in body


def test_render_approve_email():
    subject, body = render_email("approve", "VPN Access Request")
    assert "approved" in subject.lower()


def test_render_reject_email():
    subject, body = render_email("reject", "VPN Access Request")
    assert "rejected" in subject.lower()


def test_render_token_link_email():
    subject, body = render_email("token_link", "Onboarding", link="http://localhost/workflow/token/abc123")
    assert "http://localhost/workflow/token/abc123" in body


def test_render_complete_email():
    subject, body = render_email("complete", "Onboarding")
    assert "complete" in subject.lower()
    assert "account has been created" in body


def test_render_unknown_event_raises():
    with pytest.raises(ValueError, match="No email template"):
        render_email("unknown_event", "Test Workflow")


# --- Tests: full notify pipeline ---

async def test_notify_sends_to_resolved_recipients():
    """Test the full notify() pipeline: rule matching -> resolution -> rendering -> sending."""
    req = FakeRequest(
        type="vpn_access",
        current_step="request",
        target_email="target@test.com",
        created_by=uuid.uuid4(),
    )

    sent_emails = []

    async def fake_send_mail(to, subject, body_html, mail_settings):
        sent_emails.append((to, subject))

    with patch("not_dot_net.backend.mail.send_mail", side_effect=fake_send_mail):
        result = await notify(
            request=req,
            event="submit",
            step_key="request",
            workflow=VPN_WORKFLOW,
            mail_settings=MailConfig(dev_mode=True),
            get_user_email=AsyncMock(return_value="requester@test.com"),
            get_users_by_role=AsyncMock(return_value=[FakeUser("dir@test.com")]),
        )

    assert "dir@test.com" in result
    assert len(sent_emails) == 1
    assert "dir@test.com" == sent_emails[0][0]
    assert "VPN Access Request" in sent_emails[0][1]


async def test_notify_returns_empty_without_matching_rules():
    with patch("not_dot_net.backend.mail.send_mail", new_callable=AsyncMock) as send:
        result = await notify(
            request=FakeRequest(),
            event="save_draft",
            step_key="request",
            workflow=VPN_WORKFLOW,
            mail_settings=MailConfig(dev_mode=True),
            get_user_email=AsyncMock(return_value="requester@test.com"),
            get_users_by_role=AsyncMock(return_value=[FakeUser("dir@test.com")]),
        )

    assert result == []
    send.assert_not_awaited()


async def test_notify_returns_empty_without_recipients():
    with patch("not_dot_net.backend.mail.send_mail", new_callable=AsyncMock) as send:
        result = await notify(
            request=FakeRequest(),
            event="submit",
            step_key="request",
            workflow=VPN_WORKFLOW,
            mail_settings=MailConfig(dev_mode=True),
            get_user_email=AsyncMock(return_value=None),
            get_users_by_role=AsyncMock(return_value=[]),
        )

    assert result == []
    send.assert_not_awaited()


async def test_notify_uses_permission_recipients():
    workflow = WorkflowConfig(
        label="Onboarding",
        steps=[WorkflowStepConfig(key="review", type="approval", actions=["approve"])],
        notifications=[
            NotificationRuleConfig(
                event="submit",
                step="review",
                notify=["permission:access_personal_data"],
            ),
        ],
    )
    sent_emails = []

    async def fake_send_mail(to, subject, body_html, mail_settings):
        sent_emails.append((to, subject, body_html))

    with patch("not_dot_net.backend.mail.send_mail", side_effect=fake_send_mail):
        result = await notify(
            request=FakeRequest(),
            event="submit",
            step_key="review",
            workflow=workflow,
            mail_settings=MailConfig(dev_mode=True),
            get_user_email=AsyncMock(return_value=None),
            get_users_by_role=AsyncMock(return_value=[]),
            get_users_by_permission=AsyncMock(return_value=[
                FakeUser("admin@test.com"),
            ]),
        )

    assert result == ["admin@test.com"]
    assert sent_emails[0][0] == "admin@test.com"


async def test_notify_submit_with_token_uses_org_base_url():
    from not_dot_net.config import OrgConfig, org_config

    await org_config.set(OrgConfig(base_url="https://intranet.example.test/"))
    req = FakeRequest(token="token-123")
    sent_emails = []

    async def fake_send_mail(to, subject, body_html, mail_settings):
        sent_emails.append((to, subject, body_html))

    with patch("not_dot_net.backend.mail.send_mail", side_effect=fake_send_mail):
        result = await notify(
            request=req,
            event="submit",
            step_key="request",
            workflow=VPN_WORKFLOW,
            mail_settings=MailConfig(dev_mode=True),
            get_user_email=AsyncMock(return_value=None),
            get_users_by_role=AsyncMock(return_value=[FakeUser("dir@test.com")]),
        )

    assert result == ["dir@test.com"]
    assert "https://intranet.example.test/workflow/token/token-123" in sent_emails[0][2]
    assert "https://intranet.example.test//workflow" not in sent_emails[0][2]


async def test_notify_non_token_event_does_not_include_token_link():
    req = FakeRequest(token="token-should-not-be-sent")
    sent_emails = []

    async def fake_send_mail(to, subject, body_html, mail_settings):
        sent_emails.append((to, subject, body_html))

    with patch("not_dot_net.backend.mail.send_mail", side_effect=fake_send_mail):
        await notify(
            request=req,
            event="approve",
            step_key="approval",
            workflow=VPN_WORKFLOW,
            mail_settings=MailConfig(dev_mode=True),
            get_user_email=AsyncMock(return_value="requester@test.com"),
            get_users_by_role=AsyncMock(return_value=[]),
        )

    assert sent_emails
    assert "token-should-not-be-sent" not in sent_emails[0][2]


ONBOARDING_WORKFLOW = WorkflowConfig(
    label="Newcomer Onboarding",
    start_role="staff",
    target_email_field="target_email",
    steps=[
        WorkflowStepConfig(key="fill_form", type="form", assignee_role="target_person", actions=["submit"]),
        WorkflowStepConfig(key="review", type="approval", assignee_role="hr", actions=["approve", "request_corrections"]),
    ],
    notifications=[
        NotificationRuleConfig(event="request_corrections", notify=["target_person"]),
    ],
)


async def test_request_corrections_includes_token_link():
    """request_corrections notification must include the token link, not 'visit the link you received previously'."""
    token = "abc123"
    req = FakeRequest(
        type="newcomer_onboarding",
        current_step="fill_form",
        target_email="newcomer@test.com",
        token=token,
    )

    sent_emails = []

    async def fake_send_mail(to, subject, body_html, mail_settings):
        sent_emails.append((to, subject, body_html))

    with patch("not_dot_net.backend.mail.send_mail", side_effect=fake_send_mail):
        result = await notify(
            request=req,
            event="request_corrections",
            step_key="review",
            workflow=ONBOARDING_WORKFLOW,
            mail_settings=MailConfig(dev_mode=True),
            get_user_email=AsyncMock(return_value=None),
            get_users_by_role=AsyncMock(return_value=[]),
        )

    assert "newcomer@test.com" in result
    assert len(sent_emails) == 1
    _, _, body = sent_emails[0]
    assert token in body, "Token link must appear in the corrections email body"
    assert "visit the link you received previously" not in body
