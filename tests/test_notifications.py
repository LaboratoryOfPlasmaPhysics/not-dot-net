import pytest
import uuid
from unittest.mock import AsyncMock, patch
from not_dot_net.backend.notifications import notify, resolve_recipients, _display_from_email
from not_dot_net.config import (
    WorkflowConfig,
    WorkflowStepConfig,
    NotificationRuleConfig,
    FieldConfig,
)


async def _async_none():
    return None


# --- Fixtures ---

VPN_WORKFLOW = WorkflowConfig(
    label="VPN Access Request",
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


# --- Tests: _display_from_email and deep links ---

def test_display_from_email_uses_local_part():
    assert _display_from_email("marie.curie@lpp.fr") == "marie.curie"
    assert _display_from_email("") == ""


@pytest.mark.asyncio
async def test_notify_submit_includes_request_deeplink():
    req = FakeRequest(token=None, id="req-1")
    sent_bodies = []

    async def fake_send(to, subject, body):
        sent_bodies.append((to, subject, body))

    async def email_for(uid):
        return None

    async def users_by_role(role):
        class U: ...
        u = U(); u.email = "director@lpp.fr"; return [u]

    with patch("not_dot_net.backend.mail.send_mail", new=fake_send), \
         patch("not_dot_net.config.org_config.get", new=AsyncMock(
             return_value=type("O", (), {"base_url": "http://x", "app_name": "LPP"})())):
        await notify(req, "submit", "request", VPN_WORKFLOW, email_for, users_by_role)

    assert sent_bodies, "an email should be sent"
    _, _, body = sent_bodies[0]
    assert "http://x/workflow/request/req-1" in body


TOKEN_ON_SUBMIT_WORKFLOW = WorkflowConfig(
    label="Onboarding",
    target_email_field="target_email",
    steps=[
        WorkflowStepConfig(key="request", type="form", assignee_role="staff", actions=["submit"]),
        WorkflowStepConfig(key="fill", type="form", assignee="target_person", actions=["submit"]),
    ],
    notifications=[NotificationRuleConfig(event="submit", step="request", notify=["director", "target_person"])],
)


async def _send_and_collect(req, event, step_key, workflow, **kwargs) -> dict[str, str]:
    """Run notify and return {recipient: body}."""
    sent = {}

    async def fake_send(to, subject, body):
        sent[to] = body

    with patch("not_dot_net.backend.mail.send_mail", new=fake_send):
        await notify(req, event, step_key, workflow, **kwargs)
    return sent


@pytest.mark.asyncio
async def test_notify_token_link_goes_only_to_target_person():
    """Review 2026-10-12 W1: every recipient of a token-creating submit used
    to get the target person's "complete your information" link."""
    from not_dot_net.config import OrgConfig, org_config

    await org_config.set(OrgConfig(base_url="https://intranet.example.test/"))
    req = FakeRequest(token="tok-9", id="req-2", target_email="newcomer@test.com")
    sent = await _send_and_collect(
        req, "submit", "request", TOKEN_ON_SUBMIT_WORKFLOW,
        get_user_email=AsyncMock(return_value=None),
        get_users_by_role=AsyncMock(return_value=[FakeUser("dir@test.com")]),
    )

    assert "https://intranet.example.test/workflow/token/tok-9" in sent["newcomer@test.com"]
    assert "https://intranet.example.test//workflow" not in sent["newcomer@test.com"]
    assert "tok-9" not in sent["dir@test.com"]


@pytest.mark.asyncio
async def test_notify_approve_into_target_step_sends_token_link():
    """Review 2026-10-12 W1: a rule like `approve -> target_person` minted a
    token but sent the plain approve email, linking to a login-only page."""
    req = FakeRequest(token="tok-approve", target_email="newcomer@test.com")
    sent = await _send_and_collect(
        req, "approve", "approval", VPN_WORKFLOW,
        get_user_email=AsyncMock(return_value="requester@test.com"),
        get_users_by_role=AsyncMock(return_value=[]),
    )

    assert "/workflow/token/tok-approve" in sent["newcomer@test.com"]
    assert "tok-approve" not in sent["requester@test.com"]


@pytest.mark.asyncio
async def test_notify_submit_includes_requester_name():
    req = FakeRequest(token=None, id="req-3", created_by="u-1")
    sent = []

    async def fake_send(to, subject, body):
        sent.append(body)

    async def users_by_role(role):
        class U: ...
        u = U(); u.email = "director@lpp.fr"; return [u]

    async def name_for(uid):
        return "Marie Curie" if uid == "u-1" else None

    with patch("not_dot_net.backend.mail.send_mail", new=fake_send), \
         patch("not_dot_net.config.org_config.get", new=AsyncMock(
             return_value=type("O", (), {"base_url": "http://x", "app_name": "LPP"})())):
        await notify(req, "submit", "request", VPN_WORKFLOW, AsyncMock(), users_by_role,
                     get_user_name=name_for)

    assert any("Marie Curie" in b for b in sent), "submit email should name the requester"


def test_resolve_step_label_uses_step_display():
    from not_dot_net.backend.notifications import _resolve_step_label
    wf = WorkflowConfig(label="W", steps=[
        WorkflowStepConfig(key="approval", type="approval", label="Manager approval",
                           actions=["approve"]),
    ], notifications=[])
    assert _resolve_step_label(wf, "approval") == "Manager approval"


def test_resolve_step_label_falls_back_to_key_when_step_missing():
    from not_dot_net.backend.notifications import _resolve_step_label
    wf = WorkflowConfig(label="W", steps=[], notifications=[])
    assert _resolve_step_label(wf, "mystery_step") == "mystery_step"


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

    async def fake_send_mail(to, subject, body_html):
        sent_emails.append((to, subject))

    with patch("not_dot_net.backend.mail.send_mail", side_effect=fake_send_mail):
        result = await notify(
            request=req,
            event="submit",
            step_key="request",
            workflow=VPN_WORKFLOW,
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

    async def fake_send_mail(to, subject, body_html):
        sent_emails.append((to, subject, body_html))

    with patch("not_dot_net.backend.mail.send_mail", side_effect=fake_send_mail):
        result = await notify(
            request=FakeRequest(),
            event="submit",
            step_key="review",
            workflow=workflow,
            get_user_email=AsyncMock(return_value=None),
            get_users_by_role=AsyncMock(return_value=[]),
            get_users_by_permission=AsyncMock(return_value=[
                FakeUser("admin@test.com"),
            ]),
        )

    assert result == ["admin@test.com"]
    assert sent_emails[0][0] == "admin@test.com"


ONBOARDING_WORKFLOW = WorkflowConfig(
    label="Newcomer Onboarding",
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

    async def fake_send_mail(to, subject, body_html):
        sent_emails.append((to, subject, body_html))

    with patch("not_dot_net.backend.mail.send_mail", side_effect=fake_send_mail):
        result = await notify(
            request=req,
            event="request_corrections",
            step_key="review",
            workflow=ONBOARDING_WORKFLOW,
            get_user_email=AsyncMock(return_value=None),
            get_users_by_role=AsyncMock(return_value=[]),
        )

    assert "newcomer@test.com" in result
    assert len(sent_emails) == 1
    _, _, body = sent_emails[0]
    assert token in body, "Token link must appear in the corrections email body"
    assert "visit the link you received previously" not in body


@pytest.mark.asyncio
async def test_account_created_email_contains_no_password():
    """R-08: the temp password is handed over by the operator via the copy
    dialog — it must never be emailed (and thus persisted in mail_outbox)."""
    from not_dot_net.backend.email_templates import render_email

    subject, body = await render_email(
        "account_created",
        {
            "workflow_label": "Onboarding",
            "sam": "doej",
            "display_name": "Jane Doe",
            "mail": "jane@lpp.fr",
            "app_name": "LPP",
            "app_url": "http://x/",
            "recipient_name": "Jane Doe",
        },
    )
    assert "doej" in body
    assert "{initial_password}" not in body
    assert "Initial password" not in body
