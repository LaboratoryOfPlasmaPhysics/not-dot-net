"""Workflow service layer — DB operations that use the step machine engine."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel
from sqlalchemy import select, or_, and_

logger = logging.getLogger(__name__)

from not_dot_net.backend.app_config import section
from not_dot_net.backend.db import session_scope


def _token_is_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return True
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < now
from not_dot_net.backend.permissions import permission, check_permission, has_permissions

CREATE_WORKFLOWS = permission("create_workflows", "Create workflows", "Start new workflow requests")
APPROVE_WORKFLOWS = permission("approve_workflows", "Approve workflows", "Act on role-assigned workflow steps")

from not_dot_net.backend.workflow_engine import (
    compute_next_step,
    get_current_step_config,
)
from not_dot_net.backend.workflow_models import RequestStatus, WorkflowEvent, WorkflowRequest
from not_dot_net.backend.notifications import notify
from not_dot_net.config import (
    WorkflowConfig,
    WorkflowStepConfig,
    FieldConfig,
    NotificationRuleConfig,
)


class WorkflowsConfig(BaseModel):
    workflows: dict[str, WorkflowConfig] = {
        "vpn_access": WorkflowConfig(
            label="VPN Access Request",
            start_role="staff",
            target_email_field="target_email",
            steps=[
                WorkflowStepConfig(
                    key="request",
                    type="form",
                    assignee_role="staff",
                    assignee_permission="create_workflows",
                    fields=[
                        FieldConfig(name="target_name", type="text", required=True, label="Person Name"),
                        FieldConfig(name="target_email", type="email", required=True, label="Person Email"),
                        FieldConfig(name="justification", type="textarea", required=False, label="Justification"),
                    ],
                    actions=["submit"],
                ),
                WorkflowStepConfig(
                    key="approval",
                    type="approval",
                    assignee_role="director",
                    assignee_permission="approve_workflows",
                    actions=["approve", "reject"],
                ),
            ],
            notifications=[
                NotificationRuleConfig(event="submit", step="request", notify=["director"]),
                NotificationRuleConfig(event="approve", notify=["requester", "target_person"]),
                NotificationRuleConfig(event="reject", notify=["requester"]),
            ],
        ),
        "onboarding": WorkflowConfig(
            label="Onboarding",
            start_role="staff",
            target_email_field="person_email",
            steps=[
                WorkflowStepConfig(
                    key="request",
                    type="form",
                    assignee_role="staff",
                    assignee_permission="create_workflows",
                    fields=[
                        FieldConfig(name="person_name", type="text", required=True),
                        FieldConfig(name="person_email", type="email", required=True),
                        FieldConfig(name="role_status", type="select", options_key="roles", required=True),
                        FieldConfig(name="team", type="select", options_key="teams", required=True),
                        FieldConfig(name="start_date", type="date", required=True),
                        FieldConfig(name="end_date", type="date", required=False, label="End Date"),
                        FieldConfig(name="note", type="textarea", required=False),
                    ],
                    actions=["submit"],
                ),
                WorkflowStepConfig(
                    key="newcomer_info",
                    type="form",
                    assignee="target_person",
                    partial_save=True,
                    fields=[
                        FieldConfig(name="id_document", type="file", required=True, label="ID Copy"),
                        FieldConfig(name="rib", type="file", required=True, label="Bank Details (RIB)"),
                        FieldConfig(name="photo", type="file", required=False, label="Badge Photo"),
                        FieldConfig(name="phone", type="text", required=True),
                        FieldConfig(name="emergency_contact", type="text", required=True),
                    ],
                    actions=["submit"],
                ),
                WorkflowStepConfig(
                    key="admin_validation",
                    type="approval",
                    assignee_role="admin",
                    assignee_permission="approve_workflows",
                    actions=["approve", "reject"],
                ),
            ],
            notifications=[
                NotificationRuleConfig(event="submit", step="request", notify=["target_person"]),
                NotificationRuleConfig(event="submit", step="newcomer_info", notify=["admin"]),
                NotificationRuleConfig(event="approve", notify=["requester", "target_person"]),
                NotificationRuleConfig(event="reject", notify=["requester"]),
            ],
        ),
    }


workflows_config = section("workflows", WorkflowsConfig, label="Workflows")


async def _fire_notifications(req, event: str, step_key: str, wf):
    """Fire notifications for a workflow event. Best-effort.

    Uses a single session for all user lookups to avoid N+1 queries.
    """
    from not_dot_net.backend.db import User

    from not_dot_net.backend.mail import mail_config

    mail_cfg = await mail_config.get()

    async with session_scope() as session:
        async def get_user_email(user_id):
            user = await session.get(User, user_id)
            return user.email if user else None

        async def get_users_by_role(role_str):
            result = await session.execute(
                select(User).where(
                    User.role == role_str,
                    User.is_active == True,
                )
            )
            return list(result.scalars().all())

        await notify(
            request=req,
            event=event,
            step_key=step_key,
            workflow=wf,
            mail_settings=mail_cfg,
            get_user_email=get_user_email,
            get_users_by_role=get_users_by_role,
        )


async def _get_workflow_config(workflow_type: str):
    cfg = await workflows_config.get()
    wf = cfg.workflows.get(workflow_type)
    if wf is None:
        raise ValueError(f"Unknown workflow type: {workflow_type}")
    return wf


async def create_request(
    workflow_type: str,
    created_by: uuid.UUID,
    data: dict,
    actor=None,
) -> WorkflowRequest:
    if actor is not None:
        await check_permission(actor, CREATE_WORKFLOWS)
    wf = await _get_workflow_config(workflow_type)
    first_step = wf.steps[0].key

    # Resolve target_email from data if configured
    target_email = None
    if wf.target_email_field:
        target_email = data.get(wf.target_email_field)

    async with session_scope() as session:
        req = WorkflowRequest(
            type=workflow_type,
            current_step=first_step,
            status=RequestStatus.IN_PROGRESS,
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

        from not_dot_net.backend.audit import log_audit
        await log_audit(
            "workflow", "create",
            actor_id=created_by,
            target_type="request", target_id=req.id,
            detail=f"type={workflow_type}",
        )
        return req


async def submit_step(
    request_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    action: str,
    data: dict | None = None,
    comment: str | None = None,
    actor_user=None,
    actor_token: str | None = None,
) -> WorkflowRequest:
    """Submit an action on the current step.

    Exactly one of actor_user or actor_token must be provided for authorization.
    """
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found")

        wf = await _get_workflow_config(req.type)

        # Authorization
        if actor_token is not None:
            if req.token != actor_token or _token_is_expired(req.token_expires_at):
                raise PermissionError("Invalid or expired token")
        elif actor_user is not None:
            from not_dot_net.backend.workflow_engine import can_user_act
            if not await can_user_act(actor_user, req, wf):
                raise PermissionError("User cannot act on this step")
        else:
            raise PermissionError("No actor provided")

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
        if next_step and new_status == RequestStatus.IN_PROGRESS:
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

        # Audit
        from not_dot_net.backend.audit import log_audit
        await log_audit(
            "workflow", action,
            actor_id=actor_id,
            target_type="request", target_id=req.id,
            detail=f"step={event.step_key} status={new_status}",
        )

        # Fire notifications (after commit, best-effort)
        try:
            await _fire_notifications(req, action, event.step_key, wf)
        except Exception:
            logger.exception("Failed to send notifications for request %s", request_id)

        return req


async def save_draft(
    request_id: uuid.UUID,
    data: dict,
    actor_id: uuid.UUID | None = None,
    actor_token: str | None = None,
    actor_user=None,
) -> WorkflowRequest:
    """Save partial data on a form step with partial_save enabled."""
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found")

        wf = await _get_workflow_config(req.type)

        # Authorization
        if actor_token is not None:
            if req.token != actor_token or _token_is_expired(req.token_expires_at):
                raise PermissionError("Invalid or expired token")
        elif actor_user is not None:
            from not_dot_net.backend.workflow_engine import can_user_act
            if not await can_user_act(actor_user, req, wf):
                raise PermissionError("User cannot act on this step")
        else:
            raise PermissionError("No actor provided")

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
    async with session_scope() as session:
        return await session.get(WorkflowRequest, request_id)


async def get_request_by_token(token: str) -> WorkflowRequest | None:
    async with session_scope() as session:
        result = await session.execute(
            select(WorkflowRequest).where(
                WorkflowRequest.token == token,
                WorkflowRequest.status == RequestStatus.IN_PROGRESS,
                WorkflowRequest.token_expires_at > datetime.now(timezone.utc),
            )
        )
        return result.scalar_one_or_none()


async def list_user_requests(user_id: uuid.UUID) -> list[WorkflowRequest]:
    async with session_scope() as session:
        result = await session.execute(
            select(WorkflowRequest)
            .where(WorkflowRequest.created_by == user_id)
            .order_by(WorkflowRequest.created_at.desc())
        )
        return list(result.scalars().all())


async def list_actionable(user) -> list[WorkflowRequest]:
    """List requests where this user can act on the current step."""
    cfg = await workflows_config.get()
    filters = await _build_actionable_filters(user, cfg)
    if not filters:
        return []

    async with session_scope() as session:
        result = await session.execute(
            select(WorkflowRequest)
            .where(WorkflowRequest.status == RequestStatus.IN_PROGRESS, or_(*filters))
            .order_by(WorkflowRequest.created_at.desc())
        )
        return list(result.scalars().all())


async def list_events(request_id: uuid.UUID) -> list[WorkflowEvent]:
    async with session_scope() as session:
        result = await session.execute(
            select(WorkflowEvent)
            .where(WorkflowEvent.request_id == request_id)
            .order_by(WorkflowEvent.created_at.asc())
        )
        return list(result.scalars().all())


async def list_events_batch(
    request_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[WorkflowEvent]]:
    """Fetch events for multiple requests in one query."""
    if not request_ids:
        return {}
    async with session_scope() as session:
        result = await session.execute(
            select(WorkflowEvent)
            .where(WorkflowEvent.request_id.in_(request_ids))
            .order_by(WorkflowEvent.request_id, WorkflowEvent.created_at.asc())
        )
        events_by_req: dict[uuid.UUID, list[WorkflowEvent]] = {rid: [] for rid in request_ids}
        for ev in result.scalars().all():
            events_by_req.setdefault(ev.request_id, []).append(ev)
        return events_by_req


async def list_all_requests() -> list[WorkflowRequest]:
    """Admin-only: list all requests."""
    async with session_scope() as session:
        result = await session.execute(
            select(WorkflowRequest)
            .order_by(WorkflowRequest.created_at.desc())
        )
        return list(result.scalars().all())


def compute_step_age_days(events: list[WorkflowEvent], current_step: str) -> int:
    """Compute days since the last event on the current step (or fallback to last event)."""
    if not events:
        return 0
    # Prefer the last event on the current step
    relevant = next(
        (ev for ev in reversed(events) if ev.step_key == current_step),
        events[-1],
    )
    if relevant.created_at is None:
        return 0
    now = datetime.now(timezone.utc)
    created = relevant.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (now - created).days


async def _build_actionable_filters(user, cfg):
    """Build SQL OR-conditions for steps where user can act."""
    filters = []
    for wf_type, wf in cfg.workflows.items():
        for step in wf.steps:
            step_match = and_(
                WorkflowRequest.type == wf_type,
                WorkflowRequest.current_step == step.key,
            )
            if step.assignee_permission and await has_permissions(user, step.assignee_permission):
                filters.append(step_match)
            elif step.assignee_role and user.role == step.assignee_role:
                filters.append(step_match)
            elif step.assignee == "target_person":
                filters.append(and_(step_match, WorkflowRequest.target_email == user.email))
            elif step.assignee == "requester":
                filters.append(and_(step_match, WorkflowRequest.created_by == user.id))
    return filters


async def get_actionable_count(user) -> int:
    """Return count of requests where user can act."""
    cfg = await workflows_config.get()
    filters = await _build_actionable_filters(user, cfg)
    if not filters:
        return 0

    from sqlalchemy import func as sa_func
    async with session_scope() as session:
        result = await session.execute(
            select(sa_func.count())
            .select_from(WorkflowRequest)
            .where(WorkflowRequest.status == RequestStatus.IN_PROGRESS, or_(*filters))
        )
        return result.scalar_one()


async def resolve_actor_names(actor_ids) -> dict[uuid.UUID, str]:
    """Resolve actor UUIDs to display names. Single query."""
    unique_ids = {aid for aid in actor_ids if aid is not None}
    if not unique_ids:
        return {}
    from not_dot_net.backend.db import User as UserModel
    async with session_scope() as session:
        result = await session.execute(
            select(UserModel.id, UserModel.full_name, UserModel.email)
            .where(UserModel.id.in_(unique_ids))
        )
        return {row.id: row.full_name or row.email for row in result.all()}
