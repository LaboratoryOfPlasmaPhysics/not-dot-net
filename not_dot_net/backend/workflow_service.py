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


ALLOWED_EXTENSIONS: set[str] = {".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"}

# Magic bytes → expected extensions
_MAGIC_SIGNATURES: list[tuple[bytes, set[str]]] = [
    (b"%PDF", {".pdf"}),
    (b"\xff\xd8\xff", {".jpg", ".jpeg"}),
    (b"\x89PNG\r\n\x1a\n", {".png"}),
    (b"PK\x03\x04", {".docx"}),  # ZIP-based (OOXML)
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", {".doc"}),  # OLE2 (legacy Word)
]


def _check_magic(content: bytes, ext: str) -> bool:
    """Check if file content matches its extension via magic bytes."""
    for signature, valid_exts in _MAGIC_SIGNATURES:
        if content[:len(signature)] == signature:
            return ext in valid_exts
    return True  # no signature match → skip check (don't block unknown formats)


def validate_upload(content: bytes, filename: str, content_type: str, max_size_mb: int) -> str | None:
    """Validate file upload. Returns error message or None if valid."""
    max_bytes = max_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        return f"File too large (max {max_size_mb} MB)"
    from pathlib import PurePosixPath
    ext = PurePosixPath(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return f"File type not allowed: {ext}"
    if not _check_magic(content, ext):
        return "File content does not match its extension"
    return None


class WorkflowsConfig(BaseModel):
    token_expiry_days: int = 30
    verification_code_expiry_minutes: int = 15
    max_upload_size_mb: int = 10
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
            target_email_field="contact_email",
            document_instructions={
                "Intern": ["ID document", "Internship agreement", "Photo"],
                "PhD": ["ID document", "Bank details (RIB)", "Photo", "PhD enrollment certificate"],
                "_default": ["ID document", "Bank details (RIB)", "Photo"],
            },
            steps=[
                WorkflowStepConfig(
                    key="initiation",
                    type="form",
                    assignee="requester",
                    assignee_permission="create_workflows",
                    fields=[
                        FieldConfig(name="contact_email", type="email", required=True, label="Contact Email"),
                        FieldConfig(name="status", type="select", required=True, label="Status", options_key="employment_statuses"),
                        FieldConfig(name="employer", type="select", required=True, label="Employer", options_key="employers"),
                    ],
                    actions=["submit"],
                ),
                WorkflowStepConfig(
                    key="newcomer_info",
                    type="form",
                    assignee="target_person",
                    partial_save=True,
                    fields=[
                        FieldConfig(name="first_name", type="text", required=True, label="First Name"),
                        FieldConfig(name="last_name", type="text", required=True, label="Last Name"),
                        FieldConfig(name="phone", type="text", label="Phone"),
                        FieldConfig(name="address", type="textarea", label="Address"),
                        FieldConfig(name="emergency_contact", type="text", label="Emergency Contact"),
                        FieldConfig(name="id_document", type="file", required=True, label="ID Document", encrypted=True),
                        FieldConfig(name="bank_details", type="file", required=True, label="Bank Details (RIB)", encrypted=True),
                        FieldConfig(name="photo", type="file", label="Photo", encrypted=True),
                    ],
                    actions=["submit"],
                ),
                WorkflowStepConfig(
                    key="admin_validation",
                    type="approval",
                    assignee_permission="access_personal_data",
                    actions=["approve", "request_corrections", "reject"],
                    corrections_target="newcomer_info",
                ),
                WorkflowStepConfig(
                    key="it_account_creation",
                    type="form",
                    assignee_permission="manage_users",
                    fields=[
                        FieldConfig(name="notes", type="textarea", label="Notes"),
                    ],
                    actions=["complete"],
                ),
            ],
            notifications=[
                NotificationRuleConfig(event="submit", step="initiation", notify=["target_person"]),
                NotificationRuleConfig(event="submit", step="newcomer_info", notify=["permission:access_personal_data"]),
                NotificationRuleConfig(event="approve", step="admin_validation", notify=["permission:manage_users", "requester"]),
                NotificationRuleConfig(event="request_corrections", step="admin_validation", notify=["target_person"]),
                NotificationRuleConfig(event="reject", notify=["requester"]),
                NotificationRuleConfig(event="complete", step="it_account_creation", notify=["requester", "target_person"]),
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
    from not_dot_net.backend.permissions import has_permissions

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

        async def get_users_by_permission(perm):
            result = await session.execute(
                select(User).where(User.is_active == True)
            )
            all_users = list(result.scalars().all())
            return [u for u in all_users if await has_permissions(u, perm)]

        await notify(
            request=req,
            event=event,
            step_key=step_key,
            workflow=wf,
            mail_settings=mail_cfg,
            get_user_email=get_user_email,
            get_users_by_role=get_users_by_role,
            get_users_by_permission=get_users_by_permission,
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


async def _create_tenure_from_onboarding(req: WorkflowRequest, user_id: uuid.UUID) -> None:
    """Create a tenure record from a completed onboarding request."""
    from not_dot_net.backend.tenure_service import add_tenure
    from datetime import date as dt_date

    status = req.data.get("status")
    employer = req.data.get("employer")
    if not status or not employer:
        return

    start_date = dt_date.today()
    if req.data.get("start_date"):
        try:
            start_date = dt_date.fromisoformat(req.data["start_date"])
        except (ValueError, TypeError):
            pass

    await add_tenure(
        user_id=user_id,
        status=status,
        employer=employer,
        start_date=start_date,
    )


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
                cfg = await workflows_config.get()
                req.token_expires_at = datetime.now(timezone.utc) + timedelta(days=cfg.token_expiry_days)

        await session.commit()
        await session.refresh(req)

        # Mark encrypted files for retention on workflow completion (requires Task 8 column)
        if new_status == RequestStatus.COMPLETED:
            try:
                from not_dot_net.backend.encrypted_storage import mark_for_retention
                from not_dot_net.backend.workflow_models import WorkflowFile
                if hasattr(WorkflowFile, "encrypted_file_id"):
                    async with session_scope() as retention_session:
                        file_result = await retention_session.execute(
                            select(WorkflowFile).where(
                                WorkflowFile.request_id == req.id,
                                WorkflowFile.encrypted_file_id != None,
                            )
                        )
                        for wf_file in file_result.scalars().all():
                            await mark_for_retention(wf_file.encrypted_file_id, days=365)
            except Exception:
                logger.exception("Failed to mark files for retention for request %s", req.id)

            if req.type == "onboarding":
                try:
                    target_user_id = None
                    if req.data.get("returning_user_id"):
                        target_user_id = uuid.UUID(req.data["returning_user_id"])
                    elif req.target_email:
                        async with session_scope() as tenure_session:
                            from not_dot_net.backend.db import User as UserModel
                            result = await tenure_session.execute(
                                select(UserModel).where(UserModel.email == req.target_email)
                            )
                            target_user = result.scalar_one_or_none()
                            if target_user:
                                target_user_id = target_user.id
                    if target_user_id:
                        await _create_tenure_from_onboarding(req, target_user_id)
                except Exception:
                    logger.exception("Failed to create tenure for onboarding request %s", req.id)

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


async def cancel_request(
    request_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_user=None,
) -> WorkflowRequest:
    """Cancel a request. Only the creator can cancel their own in-progress requests."""
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found")
        if str(req.created_by) != str(actor_id):
            raise PermissionError("Only the request creator can cancel it")
        if req.status != RequestStatus.IN_PROGRESS:
            raise ValueError("Only in-progress requests can be cancelled")

        req.status = RequestStatus.CANCELLED
        req.token = None
        req.token_expires_at = None

        event = WorkflowEvent(
            request_id=req.id,
            step_key=req.current_step,
            action="cancel",
            actor_id=actor_id,
        )
        session.add(event)
        await session.commit()
        await session.refresh(req)

        from not_dot_net.backend.audit import log_audit
        await log_audit(
            "workflow", "cancel",
            actor_id=actor_id,
            target_type="request", target_id=req.id,
        )
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


async def can_view_request(user, req: WorkflowRequest) -> bool:
    """Check if user is allowed to view this request."""
    from not_dot_net.backend.workflow_engine import can_user_act
    if str(user.id) == str(req.created_by):
        return True
    if await has_permissions(user, "view_audit_log"):
        return True
    cfg = await workflows_config.get()
    wf = cfg.workflows.get(req.type)
    if wf and await can_user_act(user, req, wf):
        return True
    return False
