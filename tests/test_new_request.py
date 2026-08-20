"""Tests for the new-request tab — workflow listing and rendering."""

from types import SimpleNamespace

import pytest
from nicegui import ui
from nicegui.testing import User

from not_dot_net.backend.workflow_config import workflows_config, WorkflowsConfig
from not_dot_net.config import WorkflowConfig, WorkflowStepConfig


@pytest.fixture
async def admin_user():
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        email="admin@test",
        is_superuser=True,
        is_active=True,
        role="admin",
    )


async def test_new_request_skips_workflows_without_steps(user: User, admin_user):
    """Reproducer: a workflow with no steps must not crash the new-request page."""
    from not_dot_net.frontend.new_request import render
    await workflows_config.set(WorkflowsConfig(workflows={
        "empty": WorkflowConfig(label="Empty", steps=[]),
        "good": WorkflowConfig(label="Good", steps=[
            WorkflowStepConfig(key="s", type="form"),
        ]),
    }))

    @ui.page("/_nr_empty")
    async def _page():
        await render(admin_user)

    await user.open("/_nr_empty")
    # The "Good" workflow card must render; the "Empty" one must be skipped silently.
    await user.should_see("Good")
    await user.should_not_see("Empty")


async def test_returning_person_selection_prefills_and_submits(user: User):
    """R-02: selecting a returning person must prefill contact_email and
    deliver returning_user_id into the created request's data."""
    import uuid as _uuid
    from not_dot_net.backend.db import User as DbUser, session_scope
    from not_dot_net.config import FieldConfig
    from not_dot_net.frontend.i18n import t
    from not_dot_net.frontend.new_request import render

    async with session_scope() as session:
        returning = DbUser(
            id=_uuid.uuid4(), email="alice.returning@test.com",
            full_name="Alice Returning", hashed_password="x", role="",
            is_active=False,
        )
        session.add(returning)
        await session.commit()
        await session.refresh(returning)
    returning_id = str(returning.id)

    await workflows_config.set(WorkflowsConfig(workflows={
        "onboarding": WorkflowConfig(
            label="Onboarding", target_email_field="contact_email",
            steps=[
                WorkflowStepConfig(
                    key="initiation", type="form", assignee="requester",
                    fields=[FieldConfig(name="contact_email", type="email",
                                        required=True, label="contact_email")],
                    actions=["submit"],
                ),
                WorkflowStepConfig(key="review", type="approval", actions=["approve"]),
            ],
        ),
    }))

    async with session_scope() as session:
        actor = DbUser(
            id=_uuid.uuid4(), email="staff@test.com", hashed_password="x",
            role="", is_superuser=True, is_active=True,
        )
        session.add(actor)
        await session.commit()
        await session.refresh(actor)

    @ui.page("/_nr_returning")
    async def _page():
        await render(actor)

    await user.open("/_nr_returning")
    user.find(kind=ui.card).click()
    await user.should_see(t("search_existing"))

    user.find(t("search_by_name_email")).type("Alice").trigger("keyup")
    await user.should_see("Alice Returning")
    # click the ui.item itself — the simulation does not bubble clicks up
    # from the matched ItemSection child
    user.find(kind=ui.item).click()
    await user.should_see("Returning: Alice Returning")

    # The visible contact_email field must now hold the selected email.
    email_input = user.find(t("contact_email"), kind=ui.input).elements.pop()
    assert email_input.value == "alice.returning@test.com"

    user.find(t("submit")).click()
    await user.should_see(t("request_created"))

    from not_dot_net.backend.workflow_service import list_all_requests
    reqs = await list_all_requests()
    assert len(reqs) == 1
    assert reqs[0].target_email == "alice.returning@test.com"
    assert reqs[0].data.get("returning_user_id") == returning_id


async def test_workflow_config_has_no_dead_start_role_field():
    """R-03: start_role was advertised in the editor but never enforced.
    The field is removed; legacy persisted JSON containing it must still load."""
    wf = WorkflowConfig.model_validate(
        {"label": "X", "start_role": "staff", "steps": []}
    )
    assert not hasattr(wf, "start_role")
