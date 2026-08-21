"""Reproducer + non-regression for the actor_token leak.

Bug: save_draft was writing the cleartext target_person token into the
WorkflowEvent.actor_token column. Any admin viewing the workflow event log
saw working impersonation tokens for every save-draft action.

Fix: stop writing it; drop the column (migration 0009).
"""

from not_dot_net.backend.permissions import SYSTEM_ACTOR
import uuid

from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowEvent
from not_dot_net.backend.workflow_service import create_request, save_draft, submit_step
from tests.test_workflow_service import _create_user, _setup_roles


async def _start_onboarding_to_newcomer():
    """Drive onboarding to the target_person step. Returns (request, token)."""
    await _setup_roles()
    creator = await _create_user()
    req = await create_request(
        workflow_type="onboarding",
        created_by=creator.id,
        data={"contact_email": "bob@test.com", "status": "Intern", "employer": "CNRS"}, actor=SYSTEM_ACTOR
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


async def test_token_submission_attributed_to_target_in_audit():
    """B-32: a token-page submission has no logged-in actor (actor_id is None).
    The audit trail must still attribute it to the target person's email rather
    than recording an event with no actor at all.
    """
    from sqlalchemy import select as sa_select

    from not_dot_net.backend.audit import AuditEvent

    req, token = await _start_onboarding_to_newcomer()
    assert req.target_email, "onboarding should populate target_email"

    await submit_step(
        req.id, None, "submit",
        data={"first_name": "A", "last_name": "B"},
        actor_token=token,
    )

    async with session_scope() as session:
        events = (await session.execute(
            sa_select(AuditEvent).where(
                AuditEvent.category == "workflow", AuditEvent.action == "submit"
            )
        )).scalars().all()

    assert events, "expected an audit event for the token submission"
    ev = events[-1]
    assert ev.actor_id is None
    assert ev.actor_email == req.target_email


async def test_token_cannot_inject_unknown_data_keys():
    """R-11: a token holder must only be able to set the current step's
    declared fields — not arbitrary keys like returning_user_id (which
    decides whose tenure record is created on completion)."""
    from not_dot_net.backend.workflow_service import get_request_by_id

    req, token = await _start_onboarding_to_newcomer()

    await save_draft(
        req.id,
        data={"phone": "+33 1 23 45 67 89", "returning_user_id": "evil-draft"},
        actor_token=token,
    )
    fresh = await get_request_by_id(req.id)
    assert fresh.data.get("phone") == "+33 1 23 45 67 89"
    assert "returning_user_id" not in fresh.data

    submitted = await submit_step(
        req.id, None, "submit",
        data={"first_name": "A", "last_name": "B", "returning_user_id": "evil-submit"},
        actor_token=token,
    )
    assert submitted.data.get("first_name") == "A"
    assert "returning_user_id" not in submitted.data
