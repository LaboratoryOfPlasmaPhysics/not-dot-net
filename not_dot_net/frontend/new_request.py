"""New Request tab — pick a workflow type and fill the first step."""

import logging
import shutil
import uuid
from pathlib import Path

from nicegui import app, ui
from sqlalchemy import delete as sa_delete, select, or_

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.encrypted_storage import EncryptedFile, _resolve_encrypted_blob_path, store_encrypted
from not_dot_net.backend.field_definitions import resolve_step_fields
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.workflow_models import WorkflowEvent, WorkflowFile, WorkflowRequest
from not_dot_net.backend.workflow_service import (
    UPLOAD_ROOT,
    create_request,
    submit_step,
    validate_upload,
    workflows_config,
)
from not_dot_net.config import WorkflowStepConfig
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.workflow_step import render_step_form

_CLONE_DATE_FIELDS = {"departure_date", "return_date", "start_date", "end_date"}

StagedUpload = tuple[bytes, str, str]
logger = logging.getLogger(__name__)


async def _search_users(query: str) -> list[dict]:
    """Search all users (including inactive) by name or email."""
    if not query or len(query) < 2:
        return []
    async with session_scope() as session:
        pattern = f"%{query}%"
        result = await session.execute(
            select(User).where(
                or_(
                    User.full_name.ilike(pattern),
                    User.email.ilike(pattern),
                )
            ).limit(10)
        )
        return [
            {"id": str(u.id), "email": u.email, "name": u.full_name or u.email, "active": u.is_active}
            for u in result.scalars().all()
        ]


async def _persist_staged_uploads(
    request_id: uuid.UUID,
    step: WorkflowStepConfig,
    staged_uploads: dict[str, StagedUpload],
    uploaded_by: uuid.UUID,
) -> None:
    """Persist files uploaded while a new workflow request form was still unsaved."""
    if not staged_uploads:
        return

    resolved_fields = await resolve_step_fields(step)
    encrypted_fields = {f.name for f in resolved_fields if f.encrypted}
    upload_dir = UPLOAD_ROOT / str(request_id)

    async with session_scope() as session:
        for field_name, (content, filename, content_type) in staged_uploads.items():
            if field_name in encrypted_fields:
                enc_file = await store_encrypted(
                    content, filename, content_type, uploaded_by=uploaded_by,
                )
                wf_file = WorkflowFile(
                    request_id=request_id,
                    step_key=step.key,
                    field_name=field_name,
                    filename=filename,
                    storage_path="encrypted",
                    uploaded_by=uploaded_by,
                    encrypted_file_id=enc_file.id,
                )
            else:
                upload_dir.mkdir(parents=True, exist_ok=True)
                dest = upload_dir / filename
                dest.write_bytes(content)
                wf_file = WorkflowFile(
                    request_id=request_id,
                    step_key=step.key,
                    field_name=field_name,
                    filename=filename,
                    storage_path=str(dest),
                    uploaded_by=uploaded_by,
                )
            session.add(wf_file)
        await session.commit()


async def _discard_failed_request(request_id: uuid.UUID) -> None:
    """Best-effort cleanup for a request created before upload/submit failed."""
    blob_paths: list[Path] = []
    try:
        async with session_scope() as session:
            files = (
                await session.execute(
                    select(WorkflowFile).where(WorkflowFile.request_id == request_id)
                )
            ).scalars().all()
            encrypted_ids = [f.encrypted_file_id for f in files if f.encrypted_file_id]

            await session.execute(sa_delete(WorkflowFile).where(WorkflowFile.request_id == request_id))
            await session.execute(sa_delete(WorkflowEvent).where(WorkflowEvent.request_id == request_id))

            for encrypted_id in encrypted_ids:
                enc_file = await session.get(EncryptedFile, encrypted_id)
                if enc_file is None:
                    continue
                try:
                    blob_paths.append(_resolve_encrypted_blob_path(enc_file.storage_path))
                except ValueError:
                    logger.warning("Encrypted blob path outside storage root, row %s", encrypted_id)
                await session.delete(enc_file)

            req = await session.get(WorkflowRequest, request_id)
            if req is not None:
                await session.delete(req)
            await session.commit()
    finally:
        upload_dir = UPLOAD_ROOT / str(request_id)
        if upload_dir.exists():
            shutil.rmtree(upload_dir, ignore_errors=True)
        for blob_path in blob_paths:
            if blob_path.exists():
                try:
                    blob_path.unlink()
                except OSError:
                    logger.exception("Failed to remove encrypted blob %s", blob_path)


async def _create_and_submit_request(
    user: User,
    workflow_type: str,
    step: WorkflowStepConfig,
    data: dict,
    staged_uploads: dict[str, StagedUpload] | None = None,
):
    req = None
    try:
        req = await create_request(
            workflow_type=workflow_type,
            created_by=user.id,
            data=data,
            actor=user,
        )
        await _persist_staged_uploads(req.id, step, staged_uploads or {}, user.id)
        return await submit_step(
            request_id=req.id,
            actor_id=user.id,
            action="submit",
            data=data,
            actor_user=user,
        )
    except Exception:
        if req is not None:
            try:
                await _discard_failed_request(req.id)
            except Exception:
                logger.exception("Failed to clean up workflow request %s after submit failure", req.id)
        raise


async def render(user: User):
    """Render the new request tab content."""
    cfg = await workflows_config.get()
    clone = app.storage.user.pop("clone_prefill", None)
    container = ui.column().classes("w-full")

    can_create = await has_permissions(user, "create_workflows")

    with container:
        ui.label(t("select_workflow")).classes("text-h6 mb-4")

        for wf_key, wf_config in cfg.workflows.items():
            if not can_create:
                continue
            if not wf_config.steps:
                continue

            with ui.card().classes("w-full cursor-pointer") as card:
                ui.label(wf_config.label).classes("font-bold")

                form_container = ui.column().classes("w-full mt-2")
                form_container.set_visibility(False)
                form_container.on("click.stop", js_handler="() => {}")

                first_step = wf_config.steps[0]

                async def handle_submit(data, key=wf_key, fc=form_container, step=first_step, staged_uploads=None):
                    try:
                        await _create_and_submit_request(user, key, step, data, staged_uploads)
                    except Exception:
                        logger.exception("Failed to create workflow request %s", key)
                        ui.notify(t("request_creation_failed"), color="negative")
                        return
                    ui.notify(t("request_created"), color="positive")
                    fc.set_visibility(False)

                async def _open_form(fc=form_container, step=first_step, key=wf_key, prefill_data=None, submit_fn=handle_submit):
                    fc.clear()
                    fc.set_visibility(True)
                    with fc:
                        prefill = dict(prefill_data or {})
                        selection: dict = {}
                        rendered_fields: dict = {}
                        staged_uploads: dict[str, StagedUpload] = {}
                        uploaded_files: dict[str, str] = {}

                        if key == "onboarding":
                            def on_select(match: dict):
                                selection["returning_user_id"] = match["id"]
                                selection["email"] = match["email"]
                                email_field = rendered_fields.get("contact_email")
                                if email_field is not None:
                                    email_field.set_value(match["email"])

                            _render_returning_search(on_select)

                        async def handle_file_upload(field_name, event):
                            upload = event.file
                            content = await upload.read()
                            filename = Path(upload.name).name
                            content_type = upload.content_type or "application/octet-stream"
                            wf_cfg = await workflows_config.get()
                            error = validate_upload(content, filename, content_type, wf_cfg.max_upload_size_mb)
                            if error:
                                ui.notify(error, color="negative")
                                return
                            staged_uploads[field_name] = (content, filename, content_type)
                            uploaded_files[field_name] = filename
                            ui.notify(t("uploaded").format(filename=filename), color="positive")

                        async def submit_with_selection(data, _submit=submit_fn):
                            merged = dict(data)
                            # Link the returning person only while the email still
                            # matches the selection — editing it cancels the link.
                            if selection and data.get("contact_email") == selection.get("email"):
                                merged["returning_user_id"] = selection["returning_user_id"]
                            await _submit(merged, staged_uploads=staged_uploads)

                        wf_cfg_form = await workflows_config.get()
                        rendered_fields.update(
                            await render_step_form(
                                step,
                                prefill,
                                on_submit=submit_with_selection,
                                files=uploaded_files,
                                on_file_upload=handle_file_upload,
                                max_upload_size_mb=wf_cfg_form.max_upload_size_mb,
                            )
                        )

                async def toggle_form(fc=form_container, step=first_step, key=wf_key, open_fn=_open_form):
                    if fc.visible:
                        fc.set_visibility(False)
                    else:
                        await open_fn(fc, step, key)

                card.on("click", toggle_form)

                if clone and clone.get("type") == wf_key:
                    clone_data = {k: v for k, v in clone.get("data", {}).items() if k not in _CLONE_DATE_FIELDS}
                    ui.timer(0, lambda fc=form_container, step=first_step, key=wf_key, cd=clone_data:
                             _open_form(fc, step, key, cd), once=True)


def _render_returning_search(on_select) -> None:
    """Render returning-person search. Calls on_select(match) on selection."""
    with ui.expansion(t("search_existing"), icon="search").classes("w-full mb-2"):
        search_input = ui.input(label=t("search_by_name_email")).props("outlined dense")
        results_container = ui.column().classes("w-full")

        async def on_search(e):
            matches = await _search_users(search_input.value)
            results_container.clear()
            with results_container:
                for match in matches:
                    active_label = "" if match["active"] else " (inactive)"

                    async def select_user(m=match, lbl=active_label):
                        on_select(m)
                        search_input.value = m["name"]
                        results_container.clear()
                        with results_container:
                            ui.chip(
                                f"Returning: {m['name']}{lbl}",
                                icon="person",
                                color="blue",
                            )

                    ui.item(f"{match['name']} — {match['email']}{active_label}", on_click=select_user)

        search_input.on("keyup", on_search, throttle=0.3)
