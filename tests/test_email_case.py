"""A5: email comparison must be case-insensitive on auth-critical paths.

Background: AD often returns mixed-case `mail`; users sometimes type
`Alice@LPP.fr`. If we compared case-sensitively, the target_person could be
locked out of their own workflow step.
"""

import uuid

from not_dot_net.backend.workflow_service import (
    create_request,
    list_actionable,
)
from not_dot_net.backend.workflow_engine import can_user_act
from not_dot_net.backend.workflow_models import WorkflowRequest
from not_dot_net.config import WorkflowConfig, WorkflowStepConfig
from tests.test_workflow_service import _create_user, _setup_roles


def _onboarding_wf():
    return WorkflowConfig(
        label="Onboarding",
        target_email_field="contact_email",
        steps=[
            WorkflowStepConfig(
                key="newcomer_info", type="form", assignee="target_person",
                actions=["submit"],
            ),
        ],
    )


async def test_can_user_act_target_person_case_insensitive():
    """target_person check must ignore case differences in email."""
    wf = _onboarding_wf()
    user = await _create_user(email="alice@lpp.fr")
    req = WorkflowRequest(
        type="onboarding",
        current_step="newcomer_info",
        target_email="Alice@LPP.fr",  # mixed case — common when set by AD/admin
        created_by=uuid.uuid4(),
    )
    assert await can_user_act(user, req, wf), (
        "case mismatch in target_email locks the user out of their own step"
    )


async def test_create_request_normalizes_target_email():
    """target_email must be persisted in canonical (lowercase) form."""
    await _setup_roles()
    creator = await _create_user()
    req = await create_request(
        workflow_type="vpn_access",
        created_by=creator.id,
        data={"target_name": "Bob", "target_email": "Bob@Example.COM"},
    )
    assert req.target_email == "bob@example.com"


async def test_list_actionable_target_person_case_insensitive():
    """list_actionable matches by email regardless of case."""
    await _setup_roles()
    creator = await _create_user(email="creator@test.com", role="admin")
    target = await _create_user(email="newcomer@LPP.fr", role="member")
    req = await create_request(
        workflow_type="onboarding",
        created_by=creator.id,
        data={
            "contact_email": "Newcomer@lpp.fr",  # mismatched casing
            "status": "Intern",
            "employer": "CNRS",
        },
    )
    # Drive the workflow to newcomer_info (the target_person step)
    from not_dot_net.backend.workflow_service import submit_step
    creator.is_superuser = True
    await submit_step(req.id, creator.id, "submit", data={}, actor_user=creator)

    actionable = await list_actionable(target)
    assert any(r.id == req.id for r in actionable), (
        "target_person not seeing their own actionable item due to case"
    )
