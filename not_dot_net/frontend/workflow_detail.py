"""Request detail page — timeline-centered view with action panel."""

import uuid
from typing import Optional

from fastapi import Depends
from nicegui import app, ui

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.users import current_active_user_optional
from not_dot_net.backend.workflow_engine import can_user_act, get_current_step_config
from not_dot_net.backend.workflow_models import WorkflowFile
from not_dot_net.backend.workflow_service import (
    cancel_request,
    can_view_request,
    compute_step_age_days,
    get_request_by_id,
    list_events,
    resolve_actor_names,
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
        actor_names = await resolve_actor_names([ev.actor_id for ev in events])

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
            _render_header(req, wf, age, dash_cfg, actor_names, user)

            step_config = get_current_step_config(req, wf)
            render_step_progress(req.current_step, req.status, wf.steps)

            ui.separator().classes("my-4")

            field_labels = {
                f.name: t(f.label) if f.label else f.name
                for step in wf.steps for f in step.fields
            }
            _render_timeline(events, actor_names, files_by_step, user, field_labels)

            if step_config and req.status == "in_progress":
                can_act = await can_user_act(user, req, wf)
                if can_act:
                    ui.separator().classes("my-4")
                    action_container = ui.column().classes("w-full")
                    with action_container:
                        await _render_action_panel(
                            action_container, user, req, step_config, wf, request_id,
                        )

            # Resend notification button for admin — even when they can't act on the step
            if (
                step_config
                and req.status == "in_progress"
                and step_config.assignee == "target_person"
                and req.target_email
            ):
                from not_dot_net.backend.permissions import has_permissions as _has_perms
                can_resend = (
                    await _has_perms(user, "approve_workflows")
                    or await _has_perms(user, "access_personal_data")
                    or await _has_perms(user, "manage_users")
                )
                if can_resend:
                    with ui.card().classes("w-full q-pa-md mt-2").style(
                        "background: #fff8e1; border: 1px solid #ffe082;"
                    ):
                        with ui.row().classes("items-center gap-2"):
                            async def handle_resend():
                                from not_dot_net.backend.workflow_service import resend_notification
                                try:
                                    await resend_notification(req.id, actor_user=user)
                                except Exception as e:
                                    ui.notify(str(e), color="negative")
                                    return
                                ui.notify(t("notification_resent"), color="positive")
                                ui.navigate.to(f"/workflow/request/{request_id}")

                            ui.button(
                                t("resend_notification"), icon="send",
                                on_click=handle_resend,
                            ).props("flat color=primary size=sm")
                            ui.label(f"→ {req.target_email}").classes("text-xs text-grey")


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


def _render_header(req, wf, age_days, dash_cfg, actor_names, user):
    target = req.data.get("target_name") or req.data.get("person_name") or req.target_email or ""
    creator_name = actor_names.get(req.created_by, req.created_by or "")
    is_creator = str(user.id) == str(req.created_by)

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
                if is_creator:
                    async def handle_cancel():
                        try:
                            await cancel_request(req.id, user.id, actor_user=user)
                        except Exception as e:
                            ui.notify(str(e), color="negative")
                            return
                        ui.notify(t("request_cancelled"), color="positive")
                        ui.navigate.to(f"/workflow/request/{req.id}")

                    ui.button(t("cancel"), icon="cancel", on_click=handle_cancel).props(
                        "flat color=negative size=sm"
                    )
            if is_creator:
                def handle_clone():
                    app.storage.user["clone_prefill"] = {"type": req.type, "data": dict(req.data)}
                    app.storage.user["active_tab"] = t("new_request")
                    ui.navigate.to("/")

                ui.button(t("clone_request"), icon="content_copy", on_click=handle_clone).props(
                    "flat color=primary size=sm"
                )


def _render_timeline(events, actor_names, files_by_step, user, field_labels):
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
                actor = actor_names.get(ev.actor_id, t("via_token") if ev.actor_id is None else "")
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
                                label = field_labels.get(k, k)
                                ui.label(f"{label}: {v}").classes("text-xs text-grey-8")

                step_files = files_by_step.get(ev.step_key, [])
                if step_files and ev.action in ("submit", "save_draft"):
                    for f in step_files:
                        field_label = field_labels.get(f.field_name, f.field_name)
                        if f.encrypted_file_id:
                            async def download_encrypted(fid=f.encrypted_file_id, fname=f.filename):
                                from not_dot_net.backend.permissions import has_permissions
                                if not await has_permissions(user, "access_personal_data"):
                                    ui.notify(t("access_denied"), color="negative")
                                    return
                                from not_dot_net.backend.encrypted_storage import read_encrypted
                                data, name, ctype = await read_encrypted(
                                    fid, actor_id=user.id, actor_email=user.email,
                                )
                                ui.download(data, name)
                            with ui.row().classes("items-center gap-1"):
                                ui.label(f"{field_label}:").classes("text-xs text-grey-8")
                                ui.button(
                                    f"📎 {f.filename}", on_click=download_encrypted,
                                ).props("flat dense size=sm")
                        else:
                            async def download_plain(fp=f.storage_path, fname=f.filename):
                                from not_dot_net.backend.workflow_service import _safe_upload_path
                                try:
                                    path = _safe_upload_path(fp)
                                except ValueError:
                                    ui.notify(t("access_denied"), color="negative")
                                    return
                                if path.exists():
                                    ui.download(path.read_bytes(), fname)
                            with ui.row().classes("items-center gap-1"):
                                ui.label(f"{field_label}:").classes("text-xs text-grey-8")
                                ui.button(
                                    f"📎 {f.filename}", on_click=download_plain,
                                ).props("flat dense size=sm")


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

            async def handle_corrections(comment):
                try:
                    await submit_step(req.id, user.id, "request_corrections", comment=comment, actor_user=user)
                except Exception as e:
                    ui.notify(str(e), color="negative")
                    return
                ui.notify(t("corrections_requested"), color="positive")
                ui.navigate.to(f"/workflow/request/{request_id_str}")

            corrections_fn = handle_corrections if step_config.corrections_target else None

            render_approval(req.data, wf, step_config, handle_approve, handle_reject, corrections_fn)

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


async def _load_files(request_id: uuid.UUID) -> dict[str, list[WorkflowFile]]:
    """Load all files for a request, grouped by step_key."""
    from sqlalchemy import select as sa_select
    async with session_scope() as session:
        result = await session.execute(
            sa_select(WorkflowFile).where(WorkflowFile.request_id == request_id)
        )
        files: dict[str, list[WorkflowFile]] = {}
        for f in result.scalars().all():
            files.setdefault(f.step_key, []).append(f)
        return files
