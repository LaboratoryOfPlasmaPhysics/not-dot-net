"""Workflow service layer — DB operations that use the step machine engine."""

import logging
import os
import re
import secrets
import shutil
import string
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

UPLOAD_ROOT = Path(os.environ.get("NDN_DATA_DIR", "data")) / "uploads"


# --- AD account creation helpers ---

def _normalize_name(s: str) -> str:
    """Lowercase + accent-strip + drop non-alphanumeric."""
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFKD", s)
    no_accent = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", no_accent.lower())


def derive_sam_candidates(first_name: str, last_name: str, max_steps: int = 5) -> list[str]:
    """Return sAM candidates in cascading order: {last}, {last}{first[0]}, {last}{first[:2]}, ..."""
    last = _normalize_name(last_name)
    first = _normalize_name(first_name)
    candidates = [last]
    for i in range(1, min(len(first), max_steps) + 1):
        candidates.append(f"{last}{first[:i]}")
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def render_mail(template: str, first_name: str, last_name: str) -> str:
    return template.format(first=_normalize_name(first_name), last=_normalize_name(last_name))


def render_home(template: str, sam: str) -> str:
    return template.format(sam=sam)


def generate_initial_password(length: int = 16) -> str:
    """Strong password with at least one upper, lower, digit, symbol — passes AD complexity."""
    alpha = string.ascii_letters
    digits = string.digits
    symbols = "!@#$%^&*-_=+"
    pool = alpha + digits + symbols
    while True:
        pwd = "".join(secrets.choice(pool) for _ in range(length))
        if (any(c.islower() for c in pwd) and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd) and any(c in symbols for c in pwd)):
            return pwd


# LDAP primitives — imported directly so tests can monkeypatch them via this module's namespace.
from not_dot_net.backend.auth.ldap import (  # noqa: F401
    ldap_config as _ldap_cfg_section,
    ldap_user_exists_by_sam,
    ldap_create_user,
    ldap_add_to_groups,
    NewAdUser,
    LdapModifyError,
    get_ldap_connect,
    USERNAME_RE,
)


def _safe_upload_path(stored_path: str, root: Path | None = None) -> Path:
    """Resolve a WorkflowFile.storage_path and confirm it sits under the
    upload root. Defends against corrupted DB rows pointing outside the
    expected directory.

    Returns the resolved absolute Path. Raises ValueError otherwise.
    """
    base = (root or UPLOAD_ROOT).resolve()
    candidate = Path(stored_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"Refusing to serve file outside upload root: {stored_path!r}"
        ) from exc
    return candidate


from pydantic import BaseModel, Field
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
from not_dot_net.backend.workflow_models import (
    RequestStatus,
    WorkflowEvent,
    WorkflowFile,
    WorkflowRequest,
)
from not_dot_net.backend.notifications import notify
from not_dot_net.backend.default_workflows import default_workflows
from not_dot_net.config import (
    WorkflowConfig,
    WorkflowStepConfig,
    FieldConfig,
    NotificationRuleConfig,
    StepEffectConfig,
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


async def persist_workflow_upload(
    *,
    request_id: uuid.UUID,
    step_key: str,
    field_name: str,
    content: bytes,
    filename: str,
    content_type: str,
    encrypted: bool,
    uploaded_by: uuid.UUID | None,
) -> WorkflowFile:
    """Persist one uploaded file as a new WorkflowFile (a new current version).

    Plain files go to UPLOAD_ROOT/<request_id>/<file_id>/<filename> — a unique
    folder per upload — so a re-upload, or a same-named file in another field,
    never overwrites an earlier version's blob on disk. Encrypted files already
    get a unique blob per call via store_encrypted.
    """
    file_id = uuid.uuid4()
    written_paths: list[Path] = []
    async with session_scope() as session:
        try:
            if encrypted:
                from not_dot_net.backend.encrypted_storage import prepare_encrypted_file_record
                enc_file, blob_path = prepare_encrypted_file_record(
                    content, filename, content_type, uploaded_by,
                )
                written_paths.append(blob_path)
                session.add(enc_file)
                wf_file = WorkflowFile(
                    id=file_id, request_id=request_id, step_key=step_key,
                    field_name=field_name, filename=filename, storage_path="encrypted",
                    uploaded_by=uploaded_by, encrypted_file_id=enc_file.id,
                )
            else:
                dest_dir = UPLOAD_ROOT / str(request_id) / str(file_id)
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / filename
                dest.write_bytes(content)
                written_paths.append(dest)
                wf_file = WorkflowFile(
                    id=file_id, request_id=request_id, step_key=step_key,
                    field_name=field_name, filename=filename, storage_path=str(dest),
                    uploaded_by=uploaded_by,
                )
            session.add(wf_file)
            await session.commit()
            await session.refresh(wf_file)
            return wf_file
        except Exception:
            await session.rollback()
            for path in written_paths:
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    logger.exception("Failed to clean up workflow upload path %s", path)
            raise


class WorkflowsConfig(BaseModel):
    token_expiry_days: int = 30
    verification_code_expiry_minutes: int = 15
    max_upload_size_mb: int = 10
    workflows: dict[str, WorkflowConfig] = Field(default_factory=default_workflows)


workflows_config = section("workflows", WorkflowsConfig, label="Workflows")


async def _send_token_link(req, wf):
    """Send the token link email directly to the target person."""
    from not_dot_net.backend.mail import send_mail
    from not_dot_net.backend.email_templates import render_email
    from not_dot_net.config import org_config

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
    await send_mail(req.target_email, subject, body)


async def _fire_notifications(req, event: str, step_key: str, wf):
    """Fire notifications for a workflow event. Best-effort.

    Uses a single session for all user lookups to avoid N+1 queries.
    """
    from not_dot_net.backend.db import User
    from not_dot_net.backend.permissions import has_permissions

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
    from not_dot_net.backend.field_definitions import resolve_step_fields
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
    if actor is not None:
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
            from not_dot_net.backend.workflow_engine import can_user_act
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
            from not_dot_net.backend.workflow_effects import ensure_effect_credentials
            ensure_effect_credentials(step_cfg, action, ad_creds)

        next_step, new_status = compute_next_step(wf, req.current_step, action)

        # Handle ad_account_creation step type before the standard transition
        if getattr(step_cfg, "type", None) == "ad_account_creation" and action == "complete":
            if not ad_creds:
                from not_dot_net.backend.workflow_effects import AdCredentialsRequired
                raise AdCredentialsRequired("ad_account_creation step requires AD admin credentials")
            ad_result = await _handle_ad_account_creation(
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
                req.token_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=cfg.token_expiry_days)
                # Reset verification code state — old code must not be reusable
                # against the freshly minted token URL.
                req.verification_code_hash = None
                req.code_expires_at = None
                req.code_attempts = 0

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
                            from sqlalchemy import func as sa_func
                            result = await tenure_session.execute(
                                select(UserModel).where(
                                    sa_func.lower(UserModel.email)
                                    == req.target_email.strip().lower()
                                )
                            )
                            target_user = result.scalar_one_or_none()
                            if target_user:
                                target_user_id = target_user.id
                    if target_user_id:
                        await _create_tenure_from_onboarding(req, target_user_id)
                except Exception:
                    logger.exception("Failed to create tenure for onboarding request %s", req.id)

        # Audit. Token submissions have no logged-in actor (actor_id is None);
        # attribute them to the target person's email so the trail isn't blank.
        from not_dot_net.backend.audit import log_audit
        await log_audit(
            "workflow", action,
            actor_id=actor_id,
            actor_email=req.target_email if actor_token is not None else None,
            target_type="request", target_id=req.id,
            detail=f"step={event.step_key} status={new_status}",
        )

        # Fire any AD effects declared on the step for this action.
        if getattr(step_cfg, "effects", None):
            from not_dot_net.backend.workflow_effects import run_effects
            await run_effects(
                request=req, step=step_cfg, action=action,
                ad_creds=ad_creds, actor=actor_user,
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


async def delete_request(request_id: uuid.UUID, actor_user) -> None:
    """Hard-delete a request with its events and files (DB rows, plain uploads,
    encrypted blobs). Superuser only — meant for purging test requests made in
    real conditions. Audit log entries survive; the deletion itself is logged."""
    if not getattr(actor_user, "is_superuser", False):
        raise PermissionError("Only superusers can delete requests")

    from sqlalchemy import delete as sa_delete
    from not_dot_net.backend.encrypted_storage import (
        EncryptedFile,
        _resolve_encrypted_blob_path,
    )

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
    upload_dir = UPLOAD_ROOT / str(request_id)
    if upload_dir.exists():
        shutil.rmtree(upload_dir, ignore_errors=True)

    from not_dot_net.backend.audit import log_audit
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
            from not_dot_net.backend.workflow_engine import can_user_act
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
    from sqlalchemy import func as sa_func
    from not_dot_net.backend.workflow_engine import effective_assignee
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
    from not_dot_net.backend.db import resolve_user_names
    return await resolve_user_names(actor_ids)


async def resend_notification(
    request_id: uuid.UUID,
    actor_user=None,
) -> WorkflowRequest:
    """Regenerate token and re-send notification for the current step.

    Only works when the current step is assigned to target_person.
    """
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)
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

        can_act = (
            await has_permissions(actor_user, APPROVE_WORKFLOWS)
            or await has_permissions(actor_user, "access_personal_data")
            or await has_permissions(actor_user, "manage_users")
        )
        if not can_act:
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
        logger.exception("Failed to send notification for resend on request %s", request_id)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "workflow", "resend_notification",
        actor_id=actor_user.id, actor_email=actor_user.email,
        target_type="request", target_id=req.id,
        detail=f"step={req.current_step}",
    )

    return req


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


@dataclass(frozen=True)
class AdAccountCreationResult:
    request_id: uuid.UUID
    new_dn: str
    sam_account: str
    uid: int
    initial_password: str
    group_failures: dict[str, str]


async def _handle_ad_account_creation(
    request,
    form_data: dict,
    ad_creds: tuple[str, str],
    actor_user,
) -> AdAccountCreationResult:
    """Allocate UID → create AD user → write back → apply groups.

    Raises on AD create failure (step stays pending). Group-add failures are returned, not raised.
    """
    import not_dot_net.backend.workflow_service as _ws
    from not_dot_net.backend.uid_allocator import allocate_uid
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.db import session_scope, User
    from not_dot_net.backend.audit import log_audit
    from sqlalchemy import func, select

    # Use module-level names so tests can monkeypatch them.
    _ldap_user_exists = _ws.ldap_user_exists_by_sam
    _ldap_create = _ws.ldap_create_user
    _ldap_add_groups = _ws.ldap_add_to_groups
    _NewAdUser = _ws.NewAdUser
    _LdapModifyError = _ws.LdapModifyError
    _connect = _ws.get_ldap_connect()

    ad_cfg = await ad_account_config.get()
    ldap_cfg = await _ws._ldap_cfg_section.get()
    bind_user, bind_pw = ad_creds

    sam = form_data["sam_account"].strip()
    if not sam or not USERNAME_RE.fullmatch(sam):
        raise ValueError(f"Invalid sAMAccountName: {sam!r} — must match [a-zA-Z0-9._-]{{1,64}}")
    # ldap3 is synchronous — run AD round-trips off the event loop so a slow
    # DC doesn't freeze every connected client.
    import asyncio
    if await asyncio.to_thread(_ldap_user_exists, sam, bind_user, bind_pw, ldap_cfg, _connect):
        raise ValueError(f"sAMAccountName already exists in AD: {sam}")

    ou_dn = form_data["ou_dn"]
    if ou_dn not in ad_cfg.users_ous:
        raise ValueError(f"OU not in eligible list: {ou_dn}")

    chosen_groups = list(form_data.get("groups") or [])
    bad_groups = [g for g in chosen_groups if g not in ad_cfg.eligible_groups]
    if bad_groups:
        raise ValueError(f"groups not in eligible_groups: {bad_groups}")

    async with session_scope() as session:
        target = (await session.execute(
            select(User).where(func.lower(User.email) == (request.target_email or "").lower())
        )).scalar_one_or_none()
    if not target:
        raise ValueError(f"No local User for target_email={request.target_email!r}")

    uid = await allocate_uid(target.id, sam)

    first = form_data["first_name"]
    last = form_data["last_name"]
    display_name = form_data.get("display_name") or f"{first} {last}"
    initial_password = generate_initial_password(ad_cfg.password_length)

    new_user = _NewAdUser(
        sam_account=sam,
        given_name=first,
        surname=last,
        display_name=display_name,
        mail=form_data["mail"],
        description=form_data.get("description"),
        ou_dn=ou_dn,
        uid_number=uid,
        gid_number=int(form_data.get("gid_number") or ad_cfg.default_gid_number),
        login_shell=form_data.get("login_shell") or ad_cfg.default_login_shell,
        home_directory=form_data["home_directory"],
        initial_password=initial_password,
        must_change_password=True,
    )
    try:
        new_dn = await asyncio.to_thread(_ldap_create, new_user, bind_user, bind_pw, ldap_cfg, _connect)
    except _LdapModifyError as e:
        await log_audit(
            category="ad", action="create_user",
            actor_id=str(actor_user.id) if actor_user else None,
            target_id=str(target.id),
            detail=f"sam={sam} uid={uid} error={e} succeeded=False",
        )
        raise

    async with session_scope() as session:
        u = await session.get(User, target.id)
        if u is not None:
            u.ldap_dn = new_dn
            u.ldap_username = sam
            u.uid_number = uid
            u.gid_number = new_user.gid_number
            u.description = new_user.description
            u.is_active = True
            await session.commit()

    await log_audit(
        category="ad", action="create_user",
        actor_id=str(actor_user.id) if actor_user else None,
        target_id=str(target.id),
        detail=f"sam={sam} uid={uid} dn={new_dn} ou={ou_dn} succeeded=True",
    )

    group_failures: dict[str, str] = {}
    if chosen_groups:
        group_failures = await asyncio.to_thread(
            _ldap_add_groups, new_dn, chosen_groups, bind_user, bind_pw, ldap_cfg, _connect,
        )
        await log_audit(
            category="ad", action="add_to_groups",
            actor_id=str(actor_user.id) if actor_user else None,
            target_id=str(target.id),
            detail=f"groups={chosen_groups} failures={group_failures}",
        )

    from not_dot_net.backend.mail import send_mail
    from not_dot_net.backend.email_templates import render_email
    from not_dot_net.config import org_config as _org_config
    contact_email = (request.target_email or "").strip()
    if contact_email:
        # Look up the workflow label for the email subject.
        wf_cfg = await workflows_config.get()
        wf = wf_cfg.workflows.get(request.type)
        workflow_label = (wf.label if wf else request.type) or "Workflow"
        _org_cfg = await _org_config.get()
        _base_url = _org_cfg.base_url.rstrip("/")
        _app_name = (_org_cfg.app_name or "not-dot-net").strip() or "not-dot-net"
        ctx = {
            "app_name": _app_name,
            "app_url": f"{_base_url}/",
            "recipient_name": display_name,
            "workflow_label": workflow_label,
            "sam": sam,
            "display_name": display_name,
            "mail": new_user.mail,
        }
        subject, body = await render_email("account_created", ctx)
        await send_mail(contact_email, subject, body)

    return AdAccountCreationResult(
        request_id=request.id, new_dn=new_dn, sam_account=sam,
        uid=uid, initial_password=initial_password, group_failures=group_failures,
    )
