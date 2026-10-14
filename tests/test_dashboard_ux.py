"""Dashboard ergonomics from the 2026-10-14 UX review (D1, D5, D7)."""

from types import SimpleNamespace

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import SYSTEM_ACTOR
from not_dot_net.config import DashboardConfig
from tests.ui_order import drawn_before
from not_dot_net.backend.workflow_engine import request_display_name
from not_dot_net.frontend.dashboard import _age_badge, _status_badge, _waiting_for, render


async def test_todo_list_comes_before_the_pages(user: UiUser):
    """D1: the director must not scroll past the welcome pages every morning."""
    from not_dot_net.backend.page_service import create_page

    await create_page(title="Welcome", slug="welcome-d1", content="Hello", author_id=None, published=True, actor=SYSTEM_ACTOR)
    async with session_scope() as session:
        viewer = User(email="dash-order@test.com", hashed_password="x", is_active=True)
        session.add(viewer)
        await session.commit()
        await session.refresh(viewer)

    @ui.page("/_dash_order")
    def page():
        render(viewer)

    await user.open("/_dash_order")
    await user.should_see("Awaiting My Action")
    await user.should_see("Welcome")
    todo = user.find("Awaiting My Action").elements.pop()
    page_card = user.find("Welcome").elements.pop()
    assert drawn_before(todo, page_card)


def test_closed_requests_have_no_age_badge():
    """D5: a red '9d' on a finished request reads as 'late'."""
    cfg = DashboardConfig()
    assert _age_badge(12, "completed", cfg) == ("", "")
    assert _age_badge(12, "rejected", cfg) == ("", "")
    label, color = _age_badge(12, "in_progress", cfg)
    assert label and color == "negative"


def test_card_uses_the_newcomer_name_once_known():
    """D7: after the newcomer step, show 'Wei Zhang', not wei.zhang@cas.cn."""
    req = SimpleNamespace(data={"first_name": "Wei", "last_name": "Zhang"}, target_email="wei.zhang@cas.cn")
    assert request_display_name(req) == "Wei Zhang"


def test_card_falls_back_to_email_before_the_name_is_known():
    req = SimpleNamespace(data={}, target_email="wei.zhang@cas.cn")
    assert request_display_name(req) == "wei.zhang@cas.cn"


def test_completed_request_with_a_failed_ad_step_is_not_green():
    """I2: the person never got their VPN group, yet the badge said Completed."""
    label, color = _status_badge("completed", needs_action=True)
    assert color == "orange-9" and "action" in label.lower()
    assert _status_badge("completed", needs_action=False)[1] == "positive"
    assert _status_badge("rejected", needs_action=False)[1] == "negative"
    assert _status_badge("in_progress", needs_action=False)[1] == "primary"


async def test_first_visit_follows_the_browser_language(user: UiUser):
    """E2: nothing ever stored the request, so a French browser got English for good."""
    user.http_client.headers["accept-language"] = "fr-FR,fr;q=0.9,en;q=0.8"
    await user.open("/login")
    await user.should_see("E-mail ou identifiant AD")


def _roles():
    from not_dot_net.backend.roles import RoleDefinition
    return {
        "director": RoleDefinition(label="Director", permissions=["approve_workflows"]),
        "it": RoleDefinition(label="IT", permissions=["manage_users", "approve_workflows"]),
        "staff": RoleDefinition(label="Staff", permissions=["create_workflows"]),
    }


def test_waiting_for_names_who_blocks_the_request():
    """D4: a request stuck 6 days at 'Newcomer information' didn't say who to chase."""
    from not_dot_net.config import WorkflowStepConfig

    req = SimpleNamespace(status="in_progress", data={"first_name": "Wei", "last_name": "Zhang"},
                          target_email="wei@x")
    newcomer = WorkflowStepConfig(key="info", type="form", assignee="target_person")
    requester = WorkflowStepConfig(key="init", type="form", assignee="requester")
    approval = WorkflowStepConfig(key="ok", type="approval", assignee_permission="approve_workflows")

    assert _waiting_for(req, newcomer, _roles(), requester_name="Maxime") == "Wei Zhang"
    assert _waiting_for(req, requester, _roles(), requester_name="Maxime") == "Maxime"
    assert _waiting_for(req, approval, _roles(), requester_name="Maxime") == "Director, IT"
    # No role holds it yet: the permission's own label, never its key.
    assert _waiting_for(req, approval, {}, requester_name="Maxime") == "Approve workflows"


def test_nobody_is_waited_for_on_a_closed_request():
    from not_dot_net.config import WorkflowStepConfig

    req = SimpleNamespace(status="completed", data={}, target_email="x@y")
    step = WorkflowStepConfig(key="ok", type="approval", assignee_permission="approve_workflows")
    assert _waiting_for(req, step, _roles(), requester_name="Maxime") == ""


async def test_empty_dashboard_says_what_you_can_do(user: UiUser):
    """E3: a student saw 'Nothing pending', 'No requests yet' and nothing else."""
    async with session_scope() as session:
        student = User(email="student-e3@test.com", hashed_password="x", is_active=True)
        session.add(student)
        await session.commit()
        await session.refresh(student)

    @ui.page("/_dash_empty")
    def page():
        render(student)

    await user.open("/_dash_empty")
    await user.should_see("Find a person")
    await user.should_see("Book a resource")
    await user.should_not_see("New Request")  # no create_workflows
