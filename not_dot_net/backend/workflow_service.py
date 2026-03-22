"""Workflow service layer — DB operations that use the step machine engine."""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from not_dot_net.backend.db import get_async_session
from not_dot_net.backend.roles import Role, has_role
from not_dot_net.backend.workflow_engine import (
    compute_next_step,
    get_current_step_config,
)
from not_dot_net.backend.workflow_models import WorkflowEvent, WorkflowRequest
from not_dot_net.backend.notifications import notify
from not_dot_net.config import get_settings


async def _fire_notifications(req, event: str, step_key: str, wf):
    """Fire notifications for a workflow event. Best-effort.

    Note: each lookup opens a fresh session (N+1). Acceptable at current scale.
    """
    from not_dot_net.backend.db import get_async_session, User
    from not_dot_net.backend.roles import Role as RoleEnum

    settings = get_settings()

    async def get_user_email(user_id):
        get_session = asynccontextmanager(get_async_session)
        async with get_session() as session:
            user = await session.get(User, user_id)
            return user.email if user else None

    async def get_users_by_role(role_str):
        get_session = asynccontextmanager(get_async_session)
        async with get_session() as session:
            result = await session.execute(
                select(User).where(
                    User.role == RoleEnum(role_str),
                    User.is_active == True,
                )
            )
            return list(result.scalars().all())

    await notify(
        request=req,
        event=event,
        step_key=step_key,
        workflow=wf,
        mail_settings=settings.mail,
        get_user_email=get_user_email,
        get_users_by_role=get_users_by_role,
    )


def _get_workflow_config(workflow_type: str):
    settings = get_settings()
    wf = settings.workflows.get(workflow_type)
    if wf is None:
        raise ValueError(f"Unknown workflow type: {workflow_type}")
    return wf


async def create_request(
    workflow_type: str,
    created_by: uuid.UUID,
    data: dict,
) -> WorkflowRequest:
    wf = _get_workflow_config(workflow_type)
    first_step = wf.steps[0].key

    # Resolve target_email from data if configured
    target_email = None
    if wf.target_email_field:
        target_email = data.get(wf.target_email_field)

    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        req = WorkflowRequest(
            type=workflow_type,
            current_step=first_step,
            status="in_progress",
            data=data,
            created_by=created_by,
            target_email=target_email,
        )
        session.add(req)

        event = WorkflowEvent(
            request_id=req.id,
            step_key=first_step,
            action="create",
            actor_id=created_by,
            data_snapshot=data,
        )
        session.add(event)
        await session.commit()
        await session.refresh(req)
        return req


async def submit_step(
    request_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    data: dict | None = None,
    comment: str | None = None,
    actor_user=None,
) -> WorkflowRequest:
    """Submit an action on the current step. Pass actor_user for authorization check."""
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        req = await session.get(WorkflowRequest, request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found")

        wf = _get_workflow_config(req.type)

        # Authorization: verify actor can act on this step
        if actor_user is not None:
            from not_dot_net.backend.workflow_engine import can_user_act
            if not can_user_act(actor_user, req, wf):
                raise PermissionError("User cannot act on this step")

        next_step, new_status = compute_next_step(wf, req.current_step, action)

        # Merge new data
        if data:
            merged = dict(req.data)
            merged.update(data)
            req.data = merged

        # Log event
        event = WorkflowEvent(
            request_id=req.id,
            step_key=req.current_step,
            action=action,
            actor_id=actor_id,
            data_snapshot=data,
            comment=comment,
        )
        session.add(event)

        # Transition
        if next_step:
            req.current_step = next_step
        req.status = new_status

        # Clear token on step completion
        if action != "save_draft":
            req.token = None
            req.token_expires_at = None

        # Generate token if next step is for target_person
        if next_step and new_status == "in_progress":
            next_step_config = None
            for s in wf.steps:
                if s.key == next_step:
                    next_step_config = s
                    break
            if next_step_config and next_step_config.assignee == "target_person":
                req.token = str(uuid.uuid4())
                req.token_expires_at = datetime.now(timezone.utc) + timedelta(days=30)

        await session.commit()
        await session.refresh(req)

        # Fire notifications (after commit, best-effort)
        try:
            await _fire_notifications(req, action, event.step_key, wf)
        except Exception:
            pass  # notifications are best-effort, don't fail the step

        return req


async def save_draft(
    request_id: uuid.UUID,
    data: dict,
    actor_id: uuid.UUID | None = None,
    actor_token: str | None = None,
    actor_user=None,
) -> WorkflowRequest:
    """Save partial data on a form step with partial_save enabled."""
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        req = await session.get(WorkflowRequest, request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found")

        wf = _get_workflow_config(req.type)

        # Authorization: verify actor can act on this step
        if actor_user is not None:
            from not_dot_net.backend.workflow_engine import can_user_act
            if not can_user_act(actor_user, req, wf):
                raise PermissionError("User cannot act on this step")

        merged = dict(req.data)
        merged.update(data)
        req.data = merged

        event = WorkflowEvent(
            request_id=req.id,
            step_key=req.current_step,
            action="save_draft",
            actor_id=actor_id,
            actor_token=actor_token,
            data_snapshot=data,
        )
        session.add(event)
        await session.commit()
        await session.refresh(req)
        return req


async def get_request_by_id(request_id: uuid.UUID) -> WorkflowRequest | None:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        return await session.get(WorkflowRequest, request_id)


async def get_request_by_token(token: str) -> WorkflowRequest | None:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        result = await session.execute(
            select(WorkflowRequest).where(
                WorkflowRequest.token == token,
                WorkflowRequest.status == "in_progress",
                WorkflowRequest.token_expires_at > datetime.now(timezone.utc),
            )
        )
        return result.scalar_one_or_none()


async def list_user_requests(user_id: uuid.UUID) -> list[WorkflowRequest]:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        result = await session.execute(
            select(WorkflowRequest)
            .where(WorkflowRequest.created_by == user_id)
            .order_by(WorkflowRequest.created_at.desc())
        )
        return list(result.scalars().all())


async def list_actionable(user) -> list[WorkflowRequest]:
    """List requests where this user can act on the current step."""
    settings = get_settings()
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        result = await session.execute(
            select(WorkflowRequest)
            .where(WorkflowRequest.status == "in_progress")
            .order_by(WorkflowRequest.created_at.desc())
        )
        all_active = result.scalars().all()

    actionable = []
    for req in all_active:
        wf = settings.workflows.get(req.type)
        if wf is None:
            continue
        step = get_current_step_config(req, wf)
        if step is None:
            continue

        # Check role-based assignment
        if step.assignee_role and has_role(user, Role(step.assignee_role)):
            actionable.append(req)
            continue
        # Check contextual assignment
        if step.assignee == "target_person" and user.email == req.target_email:
            actionable.append(req)
            continue
        if step.assignee == "requester" and str(user.id) == str(req.created_by):
            actionable.append(req)

    return actionable


async def list_events(request_id: uuid.UUID) -> list[WorkflowEvent]:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        result = await session.execute(
            select(WorkflowEvent)
            .where(WorkflowEvent.request_id == request_id)
            .order_by(WorkflowEvent.created_at.asc())
        )
        return list(result.scalars().all())


async def list_all_requests() -> list[WorkflowRequest]:
    """Admin-only: list all requests."""
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        result = await session.execute(
            select(WorkflowRequest)
            .order_by(WorkflowRequest.created_at.desc())
        )
        return list(result.scalars().all())
