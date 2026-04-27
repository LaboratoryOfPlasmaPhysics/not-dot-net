"""Token page with email verification code gate."""

from nicegui import ui

from not_dot_net.backend.verification import generate_verification_code, verify_code
from not_dot_net.backend.workflow_service import (
    get_request_by_token,
    save_draft,
    submit_step,
    workflows_config,
)
from not_dot_net.backend.workflow_engine import get_current_step_config
from not_dot_net.backend.mail import mail_config, send_mail
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.workflow_step import render_step_form


def setup():
    @ui.page("/workflow/token/{token}")
    async def token_page(token: str):
        req = await get_request_by_token(token)

        if req is None:
            with ui.column().classes("absolute-center items-center"):
                ui.icon("error", size="xl", color="negative")
                ui.label(t("token_expired")).classes("text-h6")
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
                mail_cfg = await mail_config.get()
                await send_mail(
                    req.target_email,
                    f"Your verification code for {wf.label}",
                    f"<p>Your verification code is: <strong>{code}</strong></p>"
                    f"<p>This code expires in 15 minutes.</p>",
                    mail_cfg,
                )
                container.clear()
                with container:
                    _render_code_input(container, req, token, step_config, wf, send_code)

            def _render_code_input(cont, request, tok, step, workflow, resend_fn):
                ui.label(t("token_welcome")).classes("text-grey mb-4")
                ui.label("A verification code has been sent to your email.").classes("mb-2")
                code_input = ui.input(label="Verification Code").props("outlined dense maxlength=6")

                async def check_code():
                    try:
                        valid = await verify_code(request.id, code_input.value)
                    except PermissionError as e:
                        ui.notify(str(e), color="negative")
                        return
                    if valid:
                        cont.clear()
                        with cont:
                            await _render_form(cont, request, tok, step, workflow)
                    else:
                        ui.notify("Invalid or expired code", color="negative")

                with ui.row().classes("gap-2 mt-2"):
                    ui.button("Verify", on_click=check_code).props("color=primary")
                    ui.button("Resend code", on_click=resend_fn).props("flat")

            async def _render_form(cont, request, tok, step, workflow):
                status = request.data.get("status", "")
                instructions = workflow.document_instructions.get(
                    status, workflow.document_instructions.get("_default", [])
                )
                if instructions:
                    with ui.card().classes("w-full mb-4 bg-blue-50"):
                        ui.label("Required documents:").classes("font-bold text-sm")
                        for doc in instructions:
                            ui.label(f"• {doc}").classes("text-sm")

                async def handle_submit(data):
                    await submit_step(
                        request.id, actor_id=None, action="submit", data=data,
                        actor_token=tok,
                    )
                    cont.clear()
                    with cont:
                        ui.icon("check_circle", size="xl", color="positive")
                        ui.label(t("step_submitted")).classes("text-h6")

                async def handle_save_draft(data):
                    await save_draft(request.id, data=data, actor_token=tok)
                    ui.notify(t("draft_saved"), color="positive")

                await render_step_form(
                    step,
                    request.data,
                    on_submit=handle_submit,
                    on_save_draft=handle_save_draft if step.partial_save else None,
                )

            # Initial view: just a "Send me a code" button
            with container:
                ui.label(t("token_welcome")).classes("text-grey mb-4")
                ui.button("Send me a verification code", on_click=send_code).props("color=primary")
