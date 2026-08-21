"""Workflow service layer — the request lifecycle on top of the step machine.

File uploads live in `workflow_uploads`, AD account creation in
`workflow_ad_account`, and the config section in `workflow_config`.
"""

import logging
import shutil
import uuid
from datetime import date as dt_date, datetime, timedelta, timezone

from sqlalchemy import and_, delete as sa_delete, func as sa_func, or_, select

from not_dot_net.backend import workflow_uploads
from not_dot_net.backend.audit import log_audit
from not_dot_net.backend.db import User, resolve_user_names, session_scope
from not_dot_net.backend import mail
from not_dot_net.backend.email_templates import render_email
from not_dot_net.backend.encrypted_storage import (
    EncryptedFile,
    _resolve_encrypted_blob_path,
    mark_for_retention,
)
from not_dot_net.backend.field_definitions import resolve_step_fields
from not_dot_net.backend.notifications import notify
from not_dot_net.backend.permissions import (
    SYSTEM_ACTOR,
    check_permission,
    has_permissions,
    permission,
    users_with_permission,
)
from not_dot_net.backend.tenure_service import add_tenure
from not_dot_net.backend.workflow_ad_account import handle_ad_account_creation
from not_dot_net.backend.workflow_config import workflows_config
from not_dot_net.backend.workflow_effects import (
    AdCredentialsRequired,
    ensure_effect_credentials,
    run_effects,
)
from not_dot_net.backend.workflow_engine import (
    can_user_act,
    compute_next_step,
    effective_assignee,
    get_current_step_config,
)
from not_dot_net.backend.workflow_models import (
    RequestStatus,
    WorkflowEvent,
    WorkflowFile,
    WorkflowRequest,
)
from not_dot_net.config import org_config
logger = logging.getLogger(__name__)

CREATE_WORKFLOWS = permission("create_workflows", "Create workflows", "Start new workflow requests")
APPROVE_WORKFLOWS = permission("approve_workflows", "Approve workflows", "Act on role-assigned workflow steps")


def _token_is_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return True
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < now


async def _send_token_link(req, wf):
    """Send the token link email directly to the target person."""

    if not req.target_email or not req.token:
        return
    org_cfg = await org_config.get()
    base_url = org_cfg.base_url.rstrip("/")
    app_name = (org_cfg.app_name or "not-dot-net").strip() or "not-dot-net"
    ctx = {
        "app_name": app_name,
        "app_url": f"{base_url}/",
        "recipient_name": req.target_email.split("@")[0],
        "workflow_label": wf.label,
        "token_url": f"{base_url}/workflow/token/{req.token}",
    }
    subject, body = await render_email("token_link", ctx)
    await mail.send_mail(req.target_email, subject, body)


async def _fire_notifications(req, event: str, step_key: str, wf):
    """Fire notifications for a workflow event. Best-effort.

    Uses a single session for all user lookups to avoid N+1 queries.
    """

    async with session_scope() as session:
        async def get_user_email(user_id):
            user = await session.get(User, user_id)
            return user.email if user else None

        async def get_user_name(user_id):
            user = await session.get(User, user_id)
            return (user.full_name or user.email) if user else None

        async def get_users_by_role(role_str):
            result = await session.execute(
                select(User).where(
                    User.role == role_str,
                    User.is_active == True,
                )
            )
            return list(result.scalars().all())

        async def get_users_by_permission(perm):
            return await users_with_permission(session, perm)

        await notify(
            request=req,
            event=event,
            step_key=step_key,
            workflow=wf,
            get_user_email=get_user_email,
            get_users_by_role=get_users_by_role,
            get_users_by_permission=get_users_by_permission,
            get_user_name=get_user_name,
        )


async def _filter_step_data(step_cfg, data: dict | None) -> dict:
    """Restrict token-submitted data to the current step's declared fields.

    Token holders must not inject arbitrary keys into req.data (e.g.
    returning_user_id, which decides whose tenure record gets created).
    Referenced fields are resolved so their declared (definition-key) names
    are allowed.
    """
    if not data:
        return {}
    allowed = {f.name for f in await resolve_step_fields(step_cfg)}
    return {k: v for k, v in data.items() if k in allowed}


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
    await check_permission(actor, CREATE_WORKFLOWS)
    wf = await _get_workflow_config(workflow_type)
    first_step = wf.steps[0].key

    # Resolve target_email from data if configured. Normalize case so that
    # `user.email == target_email` works regardless of how the value was typed.
    target_email = None
    if wf.target_email_field:
        raw = data.get(wf.target_email_field)
        target_email = raw.strip().lower() if isinstance(raw, str) and raw else None

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
        # Flush so the request row exists before the event references it.
        # Without this, SQLAlchemy may emit the event INSERT first and
        # PostgreSQL rejects it on workflow_event_request_id_fkey.
        await session.flush()

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

        await log_audit(
            "workflow", "create",
            actor_id=created_by,
            target_type="request", target_id=req.id,
            detail=f"type={workflow_type}",
        )
        return req


async def _resolve_tenure_subject(req: WorkflowRequest, hook) -> uuid.UUID | None:
    """Whose tenure this is: the declared returning-person field, else target_email."""
    returning = req.data.get(hook.returning_user_field)
    if returning:
        try:
            return uuid.UUID(str(returning))
        except (ValueError, TypeError):
            logger.warning(
                "Request %s has an unusable %s=%r", req.id, hook.returning_user_field, returning
            )
    if not req.target_email:
        return None
    async with session_scope() as session:
        target = (await session.execute(
            select(User).where(
                sa_func.lower(User.email) == req.target_email.strip().lower()
            )
        )).scalar_one_or_none()
    return target.id if target else None


async def _record_tenure(req: WorkflowRequest, hook, user_id: uuid.UUID) -> None:
    """Create the tenure a completed request declares, reading the hook's fields."""
    status = req.data.get(hook.status_field)
    employer = req.data.get(hook.employer_field)
    if not status or not employer:
        return

    start_date = dt_date.today()
    raw_start = req.data.get(hook.start_date_field)
    if raw_start:
        try:
            start_date = dt_date.fromisoformat(raw_start)
        except (ValueError, TypeError):
            pass

    # SYSTEM_ACTOR, not the approver: recording the tenure is a consequence of
    # the workflow completing, and the approver need not hold manage_users.
    await add_tenure(
        user_id=user_id,
        status=status,
        employer=employer,
        start_date=start_date,
        actor=SYSTEM_ACTOR,
    )


async def submit_step(
    request_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    action: str,
    data: dict | None = None,
    comment: str | None = None,
    actor_user=None,
    actor_token: str | None = None,
    ad_creds: tuple[str, str] | None = None,
    _out: list | None = None,
) -> WorkflowRequest:
    """Submit an action on the current step.

    Exactly one of actor_user or actor_token must be provided for authorization.
    If _out is provided, any AdAccountCreationResult from an ad_account_creation step
    is appended to it so the caller can surface the temp password.
    """
    async with session_scope() as session:
        # Row lock: serialize concurrent submits so two actors can't both read
        # the same current_step and double-advance it (no-op on SQLite, real
        # FOR UPDATE on PostgreSQL).
        req = await session.get(WorkflowRequest, request_id, with_for_update=True)
        if req is None:
            raise ValueError(f"Request {request_id} not found")

        # Terminal requests accept no further actions — otherwise a second call
        # re-executes the action (duplicate events, re-fired notifications,
        # possibly re-run AD effects), and a rejected request could be
        # "approved" back into completed.
        if req.status != RequestStatus.IN_PROGRESS:
            raise ValueError(f"Cannot act on a request with status '{req.status}'")

        wf = await _get_workflow_config(req.type)

        # Authorization
        if actor_token is not None:
            if req.token != actor_token or _token_is_expired(req.token_expires_at):
                raise PermissionError("Invalid or expired token")
        elif actor_user is not None:
            if not await can_user_act(actor_user, req, wf):
                raise PermissionError("User cannot act on this step")
        else:
            raise PermissionError("No actor provided")

        step_cfg = get_current_step_config(req, wf)
        if step_cfg is None:
            raise ValueError(f"Unknown step '{req.current_step}' in workflow")
        allowed = set(step_cfg.actions)
        if step_cfg.partial_save:
            allowed.add("save_draft")
        if action not in allowed:
            raise ValueError(
                f"Action '{action}' is not allowed on step '{req.current_step}'"
            )

        if actor_token is not None:
            data = await _filter_step_data(step_cfg, data)

        # Effects needing AD credentials must fail before any state change —
        # the frontend prompts for credentials and retries this same call.
        if getattr(step_cfg, "effects", None):
            ensure_effect_credentials(step_cfg, action, ad_creds)

        next_step, new_status = compute_next_step(wf, req.current_step, action)

        # Handle ad_account_creation step type before the standard transition
        if getattr(step_cfg, "type", None) == "ad_account_creation" and action == "complete":
            if not ad_creds:
                raise AdCredentialsRequired("ad_account_creation step requires AD admin credentials")
            ad_result = await handle_ad_account_creation(
                request=req, form_data=data or {}, ad_creds=ad_creds, actor_user=actor_user,
            )
            if _out is not None:
                _out.append(ad_result)

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

        # Generate token if next step is for target_person. Never on save_draft:
        # the request stays on the same step, and rotating the token here would
        # silently invalidate the URL the target person is actively using.
        if next_step and new_status == RequestStatus.IN_PROGRESS and action != "save_draft":
            next_step_config = None
            for s in wf.steps:
                if s.key == next_step:
                    next_step_config = s
                    break
            if next_step_config and next_step_config.assignee == "target_person":
                req.token = str(uuid.uuid4())
                cfg = await workflows_config.get()
                req.token_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=cfg.token_expiry_days)
                # Reset verification code state — old code must not be reusable
                # against the freshly minted token URL.
                req.verification_code_hash = None
                req.code_expires_at = None
                req.code_attempts = 0

        await session.commit()
        # No post-commit session.refresh(req): the transition is already durable and
        # every attribute read below was loaded before commit (expire_on_commit=False).
        # A refresh here could raise after commit and, via the caller's discard-on-error
        # cleanup, delete a legitimately-submitted request together with its files.

        # Schedule encrypted files for eventual deletion once the request is
        # terminal — not just on COMPLETED. A cancelled or rejected request's
        # personal documents were previously kept forever: the retention purge
        # only collects rows with retained_until set.
        if new_status in _TERMINAL_STATUSES:
            await schedule_file_retention(req.id, new_status)

        # A tenure records an arrival, so only a COMPLETED request may create
        # one — rejected and cancelled mean the person never turned up. Which
        # workflows record a tenure, and under which field names, is declared
        # on the workflow (`tenure`); the engine knows no workflow keys.
        if wf.tenure is not None and new_status == RequestStatus.COMPLETED:
            try:
                subject_id = await _resolve_tenure_subject(req, wf.tenure)
                if subject_id:
                    await _record_tenure(req, wf.tenure, subject_id)
            except Exception:
                logger.exception("Failed to record tenure for request %s", req.id)

        # Audit. Token submissions have no logged-in actor (actor_id is None);
        # attribute them to the target person's email so the trail isn't blank.
        try:
            await log_audit(
                "workflow", action,
                actor_id=actor_id,
                actor_email=req.target_email if actor_token is not None else None,
                target_type="request", target_id=req.id,
                detail=f"step={event.step_key} status={new_status}",
            )
        except Exception:
            logger.exception("Failed to write workflow audit event for request %s", req.id)

        # Fire any AD effects declared on the step for this action.
        if getattr(step_cfg, "effects", None):
            try:
                await run_effects(
                    request=req, step=step_cfg, action=action,
                    ad_creds=ad_creds, actor=actor_user,
                )
            except Exception:
                logger.exception("Failed to run workflow effects for request %s", req.id)

        # Fire notifications (after commit, best-effort)
        try:
            await _fire_notifications(req, action, event.step_key, wf)
        except Exception:
            logger.exception("Failed to send notifications for request %s", request_id)

        return req



_TERMINAL_STATUSES = (
    RequestStatus.COMPLETED,
    RequestStatus.REJECTED,
    RequestStatus.CANCELLED,
)

# Completed requests keep their documents for a year (they document a real
# onboarding); requests that never completed have no reason to hold personal
# data that long.
_RETENTION_DAYS = {
    RequestStatus.COMPLETED: 365,
    RequestStatus.REJECTED: 30,
    RequestStatus.CANCELLED: 30,
}


async def schedule_file_retention(request_id: uuid.UUID, status) -> None:
    """Set a deletion deadline on every encrypted file of a terminal request.

    Best-effort: a retention failure must not undo a committed transition.
    """

    days = _RETENTION_DAYS.get(status)
    if days is None:
        return
    try:
        async with session_scope() as session:
            result = await session.execute(
                select(WorkflowFile).where(
                    WorkflowFile.request_id == request_id,
                    WorkflowFile.encrypted_file_id.is_not(None),
                )
            )
            file_ids = [f.encrypted_file_id for f in result.scalars().all()]
        for file_id in file_ids:
            await mark_for_retention(file_id, days=days)
    except Exception:
        logger.exception("Failed to mark files for retention for request %s", request_id)


async def cancel_request(
    request_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_user=None,
) -> WorkflowRequest:
    """Cancel a request. Only the creator can cancel their own in-progress requests."""
    async with session_scope() as session:
        # Row lock: the terminal-status check must not race a concurrent
        # submit_step holding this row (a cancel landing after a final
        # approve would flip a completed request to cancelled).
        req = await session.get(WorkflowRequest, request_id, with_for_update=True)
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

        await schedule_file_retention(req.id, RequestStatus.CANCELLED)

        await log_audit(
            "workflow", "cancel",
            actor_id=actor_id,
            target_type="request", target_id=req.id,
        )
        return req


async def delete_request(request_id: uuid.UUID, actor_user) -> None:
    """Hard-delete a request with its events and files (DB rows, plain uploads,
    encrypted blobs). Superuser only — meant for purging test requests made in
    real conditions. Audit log entries survive; the deletion itself is logged."""
    if not getattr(actor_user, "is_superuser", False):
        raise PermissionError("Only superusers can delete requests")

    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found")
        detail = f"type={req.type} status={req.status} target={req.target_email or ''}"

        files = (await session.execute(
            select(WorkflowFile).where(WorkflowFile.request_id == request_id)
        )).scalars().all()
        enc_ids = [f.encrypted_file_id for f in files if f.encrypted_file_id]

        await session.execute(sa_delete(WorkflowFile).where(WorkflowFile.request_id == request_id))
        await session.execute(sa_delete(WorkflowEvent).where(WorkflowEvent.request_id == request_id))

        blob_paths = []
        for enc_id in enc_ids:
            enc_file = await session.get(EncryptedFile, enc_id)
            if enc_file is None:
                continue
            try:
                blob_paths.append(_resolve_encrypted_blob_path(enc_file.storage_path))
            except ValueError:
                logger.warning("Encrypted blob path outside storage root, row %s", enc_id)
            await session.delete(enc_file)

        await session.delete(req)
        await session.commit()

    for blob_path in blob_paths:
        if blob_path.exists():
            blob_path.unlink()
    upload_dir = workflow_uploads.UPLOAD_ROOT / str(request_id)
    if upload_dir.exists():
        shutil.rmtree(upload_dir, ignore_errors=True)

    await log_audit(
        "workflow", "delete",
        actor_id=actor_user.id,
        target_type="request", target_id=request_id,
        detail=detail,
    )


async def save_draft(
    request_id: uuid.UUID,
    data: dict,
    actor_id: uuid.UUID | None = None,
    actor_token: str | None = None,
    actor_user=None,
) -> WorkflowRequest:
    """Save partial data on a form step with partial_save enabled."""
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id, with_for_update=True)
        if req is None:
            raise ValueError(f"Request {request_id} not found")

        if req.status != RequestStatus.IN_PROGRESS:
            raise ValueError(f"Cannot act on a request with status '{req.status}'")

        wf = await _get_workflow_config(req.type)

        # Authorization
        if actor_token is not None:
            if req.token != actor_token or _token_is_expired(req.token_expires_at):
                raise PermissionError("Invalid or expired token")
        elif actor_user is not None:
            if not await can_user_act(actor_user, req, wf):
                raise PermissionError("User cannot act on this step")
        else:
            raise PermissionError("No actor provided")

        step_cfg = get_current_step_config(req, wf)
        if step_cfg is None or not step_cfg.partial_save:
            raise PermissionError(
                f"Step '{req.current_step}' does not allow partial_save"
            )

        if actor_token is not None:
            data = await _filter_step_data(step_cfg, data)

        merged = dict(req.data)
        merged.update(data)
        req.data = merged

        event = WorkflowEvent(
            request_id=req.id,
            step_key=req.current_step,
            action="save_draft",
            actor_id=actor_id,
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
    if not token:
        return None
    async with session_scope() as session:
        result = await session.execute(
            select(WorkflowRequest).where(
                WorkflowRequest.token == token,
                WorkflowRequest.status == RequestStatus.IN_PROGRESS,
                WorkflowRequest.token_expires_at > datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        return result.scalar_one_or_none()


async def list_user_requests(
    user_id: uuid.UUID,
    since: datetime | None = None,
) -> list[WorkflowRequest]:
    async with session_scope() as session:
        query = (
            select(WorkflowRequest)
            .where(WorkflowRequest.created_by == user_id)
            .order_by(WorkflowRequest.created_at.desc())
        )
        if since:
            query = query.where(WorkflowRequest.created_at >= since)
        result = await session.execute(query)
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


async def list_all_requests(
    since: datetime | None = None,
) -> list[WorkflowRequest]:
    """Admin-only: list all requests."""
    async with session_scope() as session:
        query = select(WorkflowRequest).order_by(WorkflowRequest.created_at.desc())
        if since:
            query = query.where(WorkflowRequest.created_at >= since)
        result = await session.execute(query)
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
    """Build SQL OR-conditions for steps where user can act.

    Must mirror `workflow_engine.can_user_act` exactly — both resolve the
    step's assignee via `effective_assignee`.
    """
    filters = []
    user_email_lc = (user.email or "").strip().lower()
    for wf_type, wf in cfg.workflows.items():
        for step in wf.steps:
            step_match = and_(
                WorkflowRequest.type == wf_type,
                WorkflowRequest.current_step == step.key,
            )
            match effective_assignee(step):
                case ("target_person", _):
                    filters.append(and_(
                        step_match,
                        sa_func.lower(WorkflowRequest.target_email) == user_email_lc,
                    ))
                case ("requester", _):
                    filters.append(and_(step_match, WorkflowRequest.created_by == user.id))
                case ("permission", perm):
                    if await has_permissions(user, perm):
                        filters.append(step_match)
                case ("role", role):
                    if user.role == role:
                        filters.append(step_match)
    return filters


async def get_actionable_count(user) -> int:
    """Return count of requests where user can act."""
    cfg = await workflows_config.get()
    filters = await _build_actionable_filters(user, cfg)
    if not filters:
        return 0

    async with session_scope() as session:
        result = await session.execute(
            select(sa_func.count())
            .select_from(WorkflowRequest)
            .where(WorkflowRequest.status == RequestStatus.IN_PROGRESS, or_(*filters))
        )
        return result.scalar_one()


async def resolve_actor_names(actor_ids) -> dict[uuid.UUID, str]:
    """Resolve actor UUIDs to display names. Single query."""
    return await resolve_user_names(actor_ids)



async def can_resend_notification(user) -> bool:
    """Whether `user` may re-send a token link for a target_person step.

    The single source of truth for both the button's visibility and the
    service's enforcement — they used to carry separate copies of this
    OR-check and could drift apart.
    """
    return (
        await has_permissions(user, APPROVE_WORKFLOWS)
        or await has_permissions(user, "access_personal_data")
        or await has_permissions(user, "manage_users")
    )


async def resend_notification(
    request_id: uuid.UUID,
    actor_user=None,
) -> WorkflowRequest:
    """Regenerate token and re-send notification for the current step.

    Only works when the current step is assigned to target_person.
    """
    async with session_scope() as session:
        # Row lock: the status/assignee checks must not race a concurrent token
        # submit, or a fresh token could be minted on a non-target_person step.
        req = await session.get(WorkflowRequest, request_id, with_for_update=True)
        if req is None:
            raise ValueError(f"Request {request_id} not found")
        if req.status != RequestStatus.IN_PROGRESS:
            raise ValueError("Only in-progress requests can be re-notified")

        wf = await _get_workflow_config(req.type)

        if actor_user is None:
            raise PermissionError("No actor provided")

        step_config = next((s for s in wf.steps if s.key == req.current_step), None)

        if step_config is None or step_config.assignee != "target_person":
            raise ValueError(f"Current step '{req.current_step}' is not assigned to target_person")

        if not await can_resend_notification(actor_user):
            raise PermissionError("Insufficient permissions to resend notification")

        req.token = str(uuid.uuid4())
        cfg = await workflows_config.get()
        req.token_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=cfg.token_expiry_days)

        req.verification_code_hash = None
        req.code_expires_at = None
        req.code_attempts = 0

        await session.commit()
        await session.refresh(req)


    try:
        await _send_token_link(req, wf)
    except Exception:
        # Not swallowed: the token has already rotated, so the previous link is
        # dead. Reporting "notification resent" here would leave the target
        # holding a URL nobody knows is broken. The admin retries, which mints
        # a fresh token and sends again.
        logger.exception("Failed to send notification for resend on request %s", request_id)
        await log_audit(
            "workflow", "resend_notification",
            actor_id=actor_user.id, actor_email=actor_user.email,
            target_type="request", target_id=req.id,
            detail=f"step={req.current_step} send_failed=True",
        )
        raise

    await log_audit(
        "workflow", "resend_notification",
        actor_id=actor_user.id, actor_email=actor_user.email,
        target_type="request", target_id=req.id,
        detail=f"step={req.current_step}",
    )

    return req


async def can_view_request(user, req: WorkflowRequest) -> bool:
    """Check if user is allowed to view this request."""
    if str(user.id) == str(req.created_by):
        return True
    if await has_permissions(user, "view_audit_log"):
        return True
    cfg = await workflows_config.get()
    wf = cfg.workflows.get(req.type)
    if wf and await can_user_act(user, req, wf):
        return True
    return False
