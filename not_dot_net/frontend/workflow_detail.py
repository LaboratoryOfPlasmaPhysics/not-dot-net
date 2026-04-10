"""Request detail page — timeline-centered view with action panel."""

import uuid
from typing import Optional

from fastapi import Depends
from nicegui import ui

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.users import current_active_user_optional
from not_dot_net.backend.workflow_engine import can_user_act, get_current_step_config
from not_dot_net.backend.workflow_file_routes import can_view_request
from not_dot_net.backend.workflow_models import WorkflowFile
from not_dot_net.backend.workflow_service import (
    compute_step_age_days,
    get_request_by_id,
    list_events,
    submit_step,
    workflows_config,
)
from not_dot_net.config import dashboard_config
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.workflow_step import (
    render_approval,
    render_status_badge,
    render_step_form,
    render_step_progress,
    render_urgency_badge,
)


def setup():
    @ui.page("/workflow/request/{request_id}")
    async def detail_page(
        request_id: str,
        user: Optional[User] = Depends(current_active_user_optional),
    ):
        if user is None:
            ui.navigate.to("/login")
            return

        try:
            rid = uuid.UUID(request_id)
        except ValueError:
            _render_not_found()
            return

        req = await get_request_by_id(rid)
        if req is None:
            _render_not_found()
            return

        if not await can_view_request(user, req):
            _render_not_found()
            return

        cfg = await workflows_config.get()
        wf = cfg.workflows.get(req.type)
        if wf is None:
            _render_not_found()
            return

        events = await list_events(req.id)
        dash_cfg = await dashboard_config.get()
        age = compute_step_age_days(events, req.current_step)
        actor_names = await _resolve_actor_names([ev.actor_id for ev in events])

        files_by_step = await _load_files(req.id)

        ui.colors(primary="#0F52AC")
        with ui.header().classes("row items-center px-4").style(
            "background-color: #0F52AC"
        ):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat color=white"
            )
            ui.label(t("app_name")).classes("text-h6 text-white text-weight-light")

        with ui.column().classes("w-full max-w-3xl mx-auto pa-6"):
            _render_header(req, wf, age, dash_cfg, actor_names)

            step_config = get_current_step_config(req, wf)
            render_step_progress(req.current_step, req.status, wf.steps)

            ui.separator().classes("my-4")

            _render_timeline(events, actor_names, files_by_step)

            if step_config and req.status == "in_progress":
                can_act = can_user_act(user, req, wf)
                if step_config.assignee_permission:
                    can_act = can_act and await has_permissions(user, step_config.assignee_permission)
                if can_act:
                    ui.separator().classes("my-4")
                    action_container = ui.column().classes("w-full")
                    with action_container:
                        await _render_action_panel(
                            action_container, user, req, step_config, wf, request_id,
                        )


def _render_not_found():
    ui.colors(primary="#0F52AC")
    with ui.header().classes("row items-center px-4").style(
        "background-color: #0F52AC"
    ):
        ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
            "flat color=white"
        )
        ui.label(t("app_name")).classes("text-h6 text-white text-weight-light")
    with ui.column().classes("absolute-center items-center"):
        ui.icon("error", size="xl", color="negative")
        ui.label(t("page_not_found")).classes("text-h6")


def _render_header(req, wf, age_days, dash_cfg, actor_names):
    target = req.data.get("target_name") or req.data.get("person_name") or req.target_email or ""
    creator_name = actor_names.get(req.created_by, req.created_by or "")

    with ui.row().classes("w-full items-start justify-between"):
        with ui.column().classes("gap-0"):
            ui.label(f"{wf.label} — {target}").classes("text-h5 text-weight-light")
            date_str = req.created_at.strftime("%Y-%m-%d") if req.created_at else ""
            ui.label(f"{t('requested_by')}: {creator_name} · {date_str}").classes(
                "text-sm text-grey"
            )
        with ui.row().classes("items-center gap-2"):
            render_status_badge(req.status)
            if req.status == "in_progress":
                render_urgency_badge(age_days, dash_cfg.urgency_fresh_days, dash_cfg.urgency_aging_days)


def _render_timeline(events, actor_names, files_by_step):
    with ui.element("div").classes("relative ml-2 pl-5").style(
        "border-left: 2px solid #e0e0e0"
    ):
        for ev in events:
            is_last = ev == events[-1]
            dot_color = "#1976d2" if is_last else "#4caf50"

            with ui.element("div").classes("relative mb-5"):
                ui.element("div").classes("absolute").style(
                    f"left: -31px; top: 2px; width: 12px; height: 12px; "
                    f"background: {dot_color}; border-radius: 50%;"
                    + (" box-shadow: 0 0 6px rgba(25,118,210,0.5);" if is_last else "")
                )

                ts = ev.created_at.strftime("%Y-%m-%d %H:%M") if ev.created_at else ""
                actor = actor_names.get(ev.actor_id, t("via_token") if ev.actor_token else "")
                ui.label(ts).classes("text-[11px] text-grey")
                ui.label(f"{actor} — {ev.step_key}: {ev.action}").classes("font-semibold text-sm")

                if ev.comment:
                    with ui.element("div").classes("mt-1 pl-3").style(
                        "border-left: 3px solid #1976d2; background: #f5f5f5; "
                        "padding: 6px 10px; border-radius: 4px;"
                    ):
                        ui.label(f'💬 "{ev.comment}"').classes("text-xs text-grey-8")

                if ev.data_snapshot and ev.action not in ("save_draft",):
                    with ui.expansion(t("show_data")).classes("text-xs"):
                        for k, v in ev.data_snapshot.items():
                            if v:
                                ui.label(f"{k}: {v}").classes("text-xs text-grey-8")

                step_files = files_by_step.get(ev.step_key, [])
                if step_files and ev.action in ("submit", "save_draft"):
                    for f in step_files:
                        ui.link(
                            f"📎 {f.filename}",
                            f"/workflow/file/{f.id}",
                            new_tab=True,
                        ).classes("text-xs")


async def _render_action_panel(container, user, req, step_config, wf, request_id_str):
    with ui.card().classes("w-full q-pa-md").style(
        "background: #f8f9fa; border: 1px solid #e0e0e0;"
    ):
        ui.label(t("take_action")).classes(
            "text-xs text-grey uppercase tracking-wide mb-2"
        )

        if step_config.type == "approval":
            async def handle_approve(comment):
                try:
                    await submit_step(req.id, user.id, "approve", comment=comment, actor_user=user)
                except Exception as e:
                    ui.notify(str(e), color="negative")
                    return
                ui.notify(t("step_submitted"), color="positive")
                ui.navigate.to(f"/workflow/request/{request_id_str}")

            async def handle_reject(comment):
                try:
                    await submit_step(req.id, user.id, "reject", comment=comment, actor_user=user)
                except Exception as e:
                    ui.notify(str(e), color="negative")
                    return
                ui.notify(t("step_submitted"), color="positive")
                ui.navigate.to(f"/workflow/request/{request_id_str}")

            render_approval(req.data, wf, step_config, handle_approve, handle_reject)

        elif step_config.type == "form":
            async def handle_submit(data):
                try:
                    await submit_step(req.id, user.id, "submit", data=data, actor_user=user)
                except Exception as e:
                    ui.notify(str(e), color="negative")
                    return
                ui.notify(t("step_submitted"), color="positive")
                ui.navigate.to(f"/workflow/request/{request_id_str}")

            await render_step_form(step_config, req.data, on_submit=handle_submit)


async def _resolve_actor_names(actor_ids: list[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    """Resolve actor UUIDs to display names. Single query."""
    unique_ids = {aid for aid in actor_ids if aid is not None}
    if not unique_ids:
        return {}
    async with session_scope() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(User.id, User.full_name, User.email).where(User.id.in_(unique_ids))
        )
        return {
            row.id: row.full_name or row.email
            for row in result.all()
        }


async def _load_files(request_id: uuid.UUID) -> dict[str, list[WorkflowFile]]:
    """Load all files for a request, grouped by step_key."""
    async with session_scope() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(WorkflowFile).where(WorkflowFile.request_id == request_id)
        )
        files: dict[str, list[WorkflowFile]] = {}
        for f in result.scalars().all():
            files.setdefault(f.step_key, []).append(f)
        return files
