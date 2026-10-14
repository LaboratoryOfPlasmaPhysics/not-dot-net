"""Operations panel: what IT should look at, on one screen."""

import asyncio

from nicegui import ui
from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.operations import OperationsStatus, operations_status
from not_dot_net.backend.users import dev_mode
from not_dot_net.backend.workflow_config import workflows_config
from not_dot_net.backend.workflow_models import WorkflowRequest
from not_dot_net.frontend.i18n import format_datetime, t
from not_dot_net.frontend.workflow_step import request_display_name


def render():
    """Render the panel. Returns the refresh coroutine."""
    container = ui.column().classes("w-full gap-4")
    lock = asyncio.Lock()

    async def refresh():
        async with lock:
            status = await operations_status(app_dev_mode=dev_mode())
            container.clear()
            with container:
                await _draw(status)

    ui.timer(0, refresh, once=True)
    return refresh


async def _draw(status: OperationsStatus) -> None:
    _summary(status)
    _warnings(status.warnings)
    await _failed_effects(status)
    _mail(status)
    _ad_sync(status)


def _summary(status: OperationsStatus) -> None:
    with ui.row().classes("items-center gap-3"):
        ui.label(t("operations")).classes("text-h6")
        if status.problems:
            ui.badge(t("ops_problems", n=status.problems), color="negative")
        else:
            ui.badge(t("ops_all_clear"), color="positive")


def _warnings(warnings: list[str]) -> None:
    for key in warnings:
        with ui.row().classes("items-center gap-2 text-negative"):
            ui.icon("warning")
            ui.label(t(key))


async def _failed_effects(status: OperationsStatus) -> None:
    ui.label(t("ops_failed_ad_steps")).classes("text-subtitle1")
    if not status.failed_effects:
        ui.label(t("ops_none")).classes("text-grey")
        return
    requests = await _requests_by_id({e.request_id for e in status.failed_effects})
    labels = {k: wf.label for k, wf in (await workflows_config.get()).workflows.items()}
    ui.label(t("ops_failed_ad_steps_help")).classes("text-sm text-grey-8")
    for effect in status.failed_effects:
        req = requests.get(effect.request_id)
        with ui.row().classes("items-baseline gap-2 w-full"):
            if req is not None:
                ui.link(request_display_name(req), f"/workflow/request/{req.id}").classes("font-bold")
                ui.label(labels.get(req.type, req.type)).classes("text-grey")
            ui.label(f"{t('effect_kind_' + effect.kind)} — {effect.last_error or ''}").classes("text-sm")


async def _requests_by_id(ids: set) -> dict:
    async with session_scope() as session:
        rows = await session.scalars(select(WorkflowRequest).where(WorkflowRequest.id.in_(ids)))
        return {r.id: r for r in rows}


def _mail(status: OperationsStatus) -> None:
    ui.label(t("ops_mail")).classes("text-subtitle1")
    if not (status.failed_mails or status.stuck_mails):
        ui.label(t("ops_none")).classes("text-grey")
        return
    ui.label(t("ops_mail_counts", failed=status.failed_mails, stuck=status.stuck_mails))
    ui.label(t("ops_mail_where")).classes("text-sm text-grey-8")


def _ad_sync(status: OperationsStatus) -> None:
    ui.label(t("ops_last_ad_sync")).classes("text-subtitle1")
    sync = status.last_ad_sync
    if sync is None:
        ui.label(t("ops_never")).classes("text-grey")
        return
    ui.label(f"{format_datetime(sync.created_at)} — {sync.detail or ''}")
