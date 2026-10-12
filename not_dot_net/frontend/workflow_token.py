"""Token page with email verification code gate."""

from nicegui import ui

from not_dot_net.backend.verification import (
    generate_verification_code,
    has_valid_code,
    is_locked_out,
    verify_code,
)
from not_dot_net.backend.field_definitions import resolve_step_fields
from not_dot_net.backend.workflow_config import workflows_config
from not_dot_net.backend.workflow_service import (
    get_request_by_token,
    save_draft,
    submit_step,
)
from not_dot_net.backend.workflow_engine import get_current_step_config
from not_dot_net.backend.mail import send_mail
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.errors import notify_error
from not_dot_net.frontend.step_uploads import make_upload_handler
from not_dot_net.frontend.workflow_step import render_step_form



def _make_submit_handler(request_id, token: str, *, on_success):
    """Submit handler that reports failures instead of dying silently.

    The person on the other end of a token link has no account and no way to
    ask what happened: an unhandled exception here left them staring at a dead
    form with everything they had typed still in it and no idea it had failed.
    """
    async def handle_submit(data):
        try:
            await submit_step(
                request_id, actor_id=None, action="submit", data=data,
                actor_token=token,
            )
        except Exception as exc:
            notify_error(exc)
            return
        on_success()

    return handle_submit


def _make_save_draft_handler(request_id, token: str):
    async def handle_save_draft(data):
        try:
            await save_draft(request_id, data=data, actor_token=token)
        except Exception as exc:
            notify_error(exc)
            return
        ui.notify(t("draft_saved"), color="positive")

    return handle_save_draft


def setup():
    @ui.page("/workflow/token/{token}")
    async def token_page(token: str):
        req = await get_request_by_token(token)

        if req is None:
            with ui.column().classes("absolute-center items-center gap-2 text-center"):
                ui.icon("error", size="xl", color="negative")
                ui.label(t("token_expired")).classes("text-h6")
                ui.label(t("token_expired_help")).classes("text-sm text-grey")
            return

        cfg = await workflows_config.get()
        wf = cfg.workflows.get(req.type)
        if not wf:
            ui.label(t("token_expired"))
            return

        step_config = get_current_step_config(req, wf)
        if not step_config:
            ui.label(t("token_expired"))
            return

        with ui.column().classes("max-w-2xl mx-auto p-6"):
            ui.label(wf.label).classes("text-h5 mb-2")

            container = ui.column().classes("w-full")

            async def send_code():
                code = await generate_verification_code(req.id)
                if code is None:
                    if await is_locked_out(req.id):
                        ui.notify(t("too_many_attempts"), color="negative")
                    else:
                        ui.notify(t("code_already_sent"), color="info")
                    return
                wf_cfg = await workflows_config.get()
                expiry = wf_cfg.verification_code_expiry_minutes
                await send_mail(
                    req.target_email,
                    f"Your verification code for {wf.label}",
                    f"<p>Your verification code is: <strong>{code}</strong></p>"
                    f"<p>This code expires in {expiry} minutes.</p>",
                )
                container.clear()
                with container:
                    _render_code_input(container, req, token, step_config, wf, send_code)

            def _render_code_input(cont, request, tok, step, workflow, resend_fn):
                ui.label(t("token_welcome")).classes("text-grey mb-4")
                ui.label(t("code_sent")).classes("mb-2")
                code_input = ui.input(label=t("verification_code")).props("outlined dense maxlength=6")

                async def check_code():
                    try:
                        valid = await verify_code(request.id, code_input.value)
                    except PermissionError as e:
                        notify_error(e)
                        return
                    if valid:
                        cont.clear()
                        with cont:
                            await _render_form(cont, request, tok, step, workflow)
                    else:
                        ui.notify(t("invalid_code"), color="negative")

                with ui.row().classes("gap-2 mt-2"):
                    ui.button(t("verify"), on_click=check_code).props("color=primary")
                    ui.button(t("resend_code"), on_click=resend_fn).props("flat")

            async def _render_form(cont, request, tok, step, workflow):
                status = request.data.get("status", "")
                instructions = workflow.document_instructions.get(
                    status, workflow.document_instructions.get("_default", [])
                )
                if instructions:
                    with ui.card().classes("w-full mb-4 bg-blue-50"):
                        ui.label(t("required_documents") + ":").classes("font-bold text-sm")
                        for doc in instructions:
                            ui.label(f"• {doc}").classes("text-sm")

                resolved = await resolve_step_fields(step)

                async def token_still_valid() -> bool:
                    return await get_request_by_token(token) is not None

                uploaded_files, handle_file_upload = await make_upload_handler(
                    request.id, step,
                    encrypted_fields={f.name for f in resolved if f.encrypted},
                    uploaded_by=None,
                    authorize=token_still_valid,
                    denied_message="token_expired",
                )
                wf_cfg_form = await workflows_config.get()

                def _show_submitted():
                    cont.clear()
                    with cont:
                        ui.icon("check_circle", size="xl", color="positive")
                        ui.label(t("step_submitted")).classes("text-h6")

                handle_submit = _make_submit_handler(
                    request.id, tok, on_success=_show_submitted,
                )
                handle_save_draft = _make_save_draft_handler(request.id, tok)

                await render_step_form(
                    step,
                    request.data,
                    on_submit=handle_submit,
                    on_save_draft=handle_save_draft if step.partial_save else None,
                    files=uploaded_files,
                    on_file_upload=handle_file_upload,
                    max_upload_size_mb=wf_cfg_form.max_upload_size_mb,
                )

            with container:
                if await is_locked_out(req.id):
                    ui.label(t("token_welcome")).classes("text-grey mb-4")
                    ui.label(t("too_many_attempts")).classes("text-negative")
                    ui.label(t("too_many_attempts_help")).classes("text-sm text-grey")
                elif await has_valid_code(req.id):
                    _render_code_input(container, req, token, step_config, wf, send_code)
                else:
                    ui.label(t("token_welcome")).classes("text-grey mb-4")
                    ui.button(t("send_code"), on_click=send_code).props("color=primary")
