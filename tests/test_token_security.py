"""Reproducer + non-regression for the actor_token leak.

Bug: save_draft was writing the cleartext target_person token into the
WorkflowEvent.actor_token column. Any admin viewing the workflow event log
saw working impersonation tokens for every save-draft action.

Fix: stop writing it; drop the column (migration 0009).
"""

import uuid

from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowEvent
from not_dot_net.backend.workflow_service import (
    create_request,
    save_draft,
    submit_step,
)
from tests.test_workflow_service import _create_user, _setup_roles


async def _start_onboarding_to_newcomer():
    """Drive onboarding to the target_person step. Returns (request, token)."""
    await _setup_roles()
    creator = await _create_user()
    req = await create_request(
        workflow_type="onboarding",
        created_by=creator.id,
        data={"contact_email": "bob@test.com", "status": "Intern", "employer": "CNRS"},
    )
    req = await submit_step(req.id, creator.id, "submit", data={}, actor_user=creator)
    assert req.current_step == "newcomer_info"
    assert req.token, "expected a target_person token after first submit"
    return req, req.token


async def test_save_draft_does_not_persist_token_in_event_log():
    req, token = await _start_onboarding_to_newcomer()
    await save_draft(req.id, data={"phone": "+33 1 23 45"}, actor_token=token)

    async with session_scope() as session:
        events = (await session.execute(
            select(WorkflowEvent).where(WorkflowEvent.request_id == req.id)
        )).scalars().all()

    for ev in events:
        # The column is dropped; defend in depth in case schema reverts.
        leaked = getattr(ev, "actor_token", None)
        assert leaked is None, (
            f"WorkflowEvent.actor_token leaked for action={ev.action!r}"
        )


async def test_invalid_token_rejected_by_submit_step():
    req, _token = await _start_onboarding_to_newcomer()
    import pytest
    with pytest.raises(PermissionError):
        await submit_step(
            req.id, actor_id=None, action="submit",
            data={}, actor_token=str(uuid.uuid4()),
        )


async def test_invalid_token_rejected_by_save_draft():
    req, _token = await _start_onboarding_to_newcomer()
    import pytest
    with pytest.raises(PermissionError):
        await save_draft(req.id, data={"phone": "x"}, actor_token=str(uuid.uuid4()))
