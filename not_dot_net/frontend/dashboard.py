"""Dashboard tab — My Requests + Awaiting My Action."""

from nicegui import ui

from not_dot_net.backend.db import User
from not_dot_net.backend.roles import Role, has_role
from not_dot_net.backend.workflow_service import (
    list_user_requests,
    list_all_requests,
    list_actionable,
    list_events,
    submit_step,
)
from not_dot_net.backend.workflow_engine import get_current_step_config
from not_dot_net.config import get_settings
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.workflow_step import (
    render_approval,
    render_step_form,
    render_status_badge,
)


def render(user: User):
    """Render the dashboard tab content."""
    my_requests_container = ui.column().classes("w-full")
    actionable_container = ui.column().classes("w-full")

    async def refresh():
        await _render_my_requests(my_requests_container, user)
        await _render_actionable(actionable_container, user)

    ui.timer(0, refresh, once=True)


async def _render_my_requests(container, user: User):
    container.clear()
    # Admin sees all requests, others see only their own
    if has_role(user, Role.ADMIN):
        requests = await list_all_requests()
    else:
        requests = await list_user_requests(user.id)

    with container:
        ui.label(t("my_requests")).classes("text-h6 mb-2")
        if not requests:
            ui.label(t("no_requests")).classes("text-grey")
            return

        settings = get_settings()
        state = {"expanded_id": None}

        for req in requests:
            wf = settings.workflows.get(req.type)
            label = wf.label if wf else req.type
            step_config = get_current_step_config(req, wf) if wf else None
            step_label = step_config.key if step_config else req.current_step

            with ui.card().classes("w-full cursor-pointer") as card:
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.column().classes("gap-0"):
                        ui.label(label).classes("font-bold")
                        if req.target_email:
                            ui.label(f"{t('target_person')}: {req.target_email}").classes(
                                "text-sm text-grey"
                            )
                        ui.label(f"{t('current_step')}: {step_label}").classes("text-sm")
                    render_status_badge(req.status)

                detail_container = ui.column().classes("w-full mt-2")
                detail_container.set_visibility(False)

                async def toggle_expand(dc=detail_container, r=req, st=state):
                    if st["expanded_id"] == r.id:
                        dc.set_visibility(False)
                        st["expanded_id"] = None
                    else:
                        st["expanded_id"] = r.id
                        dc.set_visibility(True)
                        dc.clear()
                        with dc:
                            ui.separator()
                            events = await list_events(r.id)
                            for ev in events:
                                ts = ev.created_at.strftime("%Y-%m-%d %H:%M") if ev.created_at else ""
                                ui.label(f"{ts} — {ev.step_key}: {ev.action}").classes("text-sm")
                                if ev.comment:
                                    ui.label(f"  {ev.comment}").classes("text-sm text-grey ml-4")

                card.on("click", toggle_expand)


async def _render_actionable(container, user: User):
    container.clear()
    requests = await list_actionable(user)

    with container:
        ui.label(t("awaiting_action")).classes("text-h6 mb-2 mt-4")
        if not requests:
            ui.label(t("no_pending")).classes("text-grey")
            return

        settings = get_settings()
        for req in requests:
            wf = settings.workflows.get(req.type)
            if not wf:
                continue
            step_config = get_current_step_config(req, wf)
            if not step_config:
                continue

            with ui.card().classes("w-full") as card:
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.column().classes("gap-0"):
                        ui.label(wf.label).classes("font-bold")
                        ui.label(f"{t('current_step')}: {step_config.key}").classes("text-sm")
                        if req.target_email:
                            ui.label(f"{t('target_person')}: {req.target_email}").classes(
                                "text-sm text-grey"
                            )
                    if req.updated_at:
                        ui.label(req.updated_at.strftime("%Y-%m-%d")).classes("text-sm text-grey")

                action_container = ui.column().classes("w-full")

                async def handle_approve(comment, r=req):
                    try:
                        await submit_step(r.id, user.id, "approve", comment=comment)
                    except Exception as e:
                        ui.notify(str(e), color="negative")
                        return
                    # Notify before re-render (re-render deletes parent slots)
                    ui.notify(t("step_submitted"), color="positive")
                    await _render_actionable(container, user)

                async def handle_reject(comment, r=req):
                    try:
                        await submit_step(r.id, user.id, "reject", comment=comment)
                    except Exception as e:
                        ui.notify(str(e), color="negative")
                        return
                    ui.notify(t("step_submitted"), color="positive")
                    await _render_actionable(container, user)

                async def handle_submit(data, r=req):
                    try:
                        await submit_step(r.id, user.id, "submit", data=data)
                    except Exception as e:
                        ui.notify(str(e), color="negative")
                        return
                    ui.notify(t("step_submitted"), color="positive")
                    await _render_actionable(container, user)

                with action_container:
                    if step_config.type == "approval":
                        render_approval(req.data, wf, step_config, handle_approve, handle_reject)
                    elif step_config.type == "form":
                        render_step_form(step_config, req.data, on_submit=handle_submit)
