"""Dashboard tab — My Requests + Awaiting My Action."""

from nicegui import ui

from not_dot_net.backend.db import User
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.workflow_service import (
    list_user_requests,
    list_all_requests,
    list_actionable,
    list_events_batch,
    submit_step,
)
from not_dot_net.backend.workflow_engine import get_current_step_config, get_step_progress
from not_dot_net.backend.workflow_service import workflows_config
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.workflow_step import (
    render_approval,
    render_step_form,
    render_status_badge,
)


def render(user: User):
    """Render the dashboard tab content."""
    pages_container = ui.column().classes("w-full")
    my_requests_container = ui.column().classes("w-full")
    actionable_container = ui.column().classes("w-full")

    async def refresh():
        await _render_pages_section(pages_container)
        if user.is_active:
            await _render_my_requests(my_requests_container, user)
            await _render_actionable(actionable_container, user)

    ui.timer(0, refresh, once=True)


async def _render_pages_section(container):
    from not_dot_net.backend.page_service import list_pages

    container.clear()
    pages = await list_pages(published_only=True)
    if not pages:
        return

    with container:
        with ui.element("div").classes(
            "w-full grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-4"
        ):
            for page in pages:
                with ui.card().classes("q-py-sm q-px-md"):
                    ui.link(page.title, f"/pages/{page.slug}").classes(
                        "text-subtitle1 font-bold"
                    )
                    first_line = next(
                        (ln for ln in page.content.splitlines() if ln.strip() and not ln.startswith("#")),
                        "",
                    )
                    if first_line:
                        ui.label(first_line[:120]).classes("text-sm text-grey-8")


async def _workflow_labels() -> dict[str, str]:
    cfg = await workflows_config.get()
    return {k: wf.label for k, wf in cfg.workflows.items()}


def _format_date(dt) -> str:
    return dt.strftime("%Y-%m-%d") if dt else ""


def _target_display(req) -> str:
    name = req.data.get("target_name") or req.data.get("person_name") or ""
    return name or req.target_email or ""


# ---------------------------------------------------------------------------
# My Requests — table view
# ---------------------------------------------------------------------------

_STATUS_COLORS = {
    "in_progress": "blue",
    "completed": "positive",
    "rejected": "negative",
}


async def _render_my_requests(container, user: User):
    container.clear()

    if await has_permissions(user, "view_audit_log"):
        requests = await list_all_requests()
    else:
        requests = await list_user_requests(user.id)

    with container:
        ui.label(t("my_requests")).classes("text-h6 mb-2")
        if not requests:
            ui.label(t("no_requests")).classes("text-grey")
            return

        cfg = await workflows_config.get()
        wf_labels = await _workflow_labels()

        columns = [
            {"name": "type", "label": t("workflow_type"), "field": "type", "sortable": True, "align": "left"},
            {"name": "target", "label": t("target_person"), "field": "target", "sortable": True, "align": "left"},
            {"name": "progress", "label": t("progress"), "field": "progress", "sortable": True, "align": "center"},
            {"name": "step", "label": t("current_step"), "field": "step", "sortable": True, "align": "left"},
            {"name": "date", "label": t("created_at"), "field": "date", "sortable": True, "align": "left"},
            {"name": "status", "label": t("status"), "field": "status", "sortable": True, "align": "center"},
        ]

        events_by_req = await list_events_batch([req.id for req in requests])

        rows = []
        for req in requests:
            wf = cfg.workflows.get(req.type)
            step_config = get_current_step_config(req, wf) if wf else None
            step_label = step_config.key if step_config else req.current_step
            current, total = get_step_progress(req, wf) if wf else (0, 0)

            event_rows = [
                {
                    "ts": ev.created_at.strftime("%Y-%m-%d %H:%M") if ev.created_at else "",
                    "label": f"{ev.step_key}: {ev.action}",
                    "comment": ev.comment or "",
                }
                for ev in events_by_req.get(req.id, [])
            ]

            rows.append({
                "id": str(req.id),
                "type": wf_labels.get(req.type, req.type),
                "target": _target_display(req),
                "progress": f"{current}/{total}",
                "progress_pct": current / total if total else 0,
                "step": step_label,
                "date": _format_date(req.created_at),
                "status": req.status,
                "events": event_rows,
            })

        table = ui.table(
            columns=columns, rows=rows, row_key="id", pagination={"rowsPerPage": 15},
        ).classes("w-full")
        table.props("flat bordered dense")

        table.add_slot("body", r'''
            <q-tr :props="props" @click="props.expand = !props.expand" class="cursor-pointer">
                <q-td v-for="col in props.cols" :key="col.name" :props="props">
                    <q-badge v-if="col.name === 'status'"
                        :color="col.value === 'completed' ? 'positive' : col.value === 'rejected' ? 'negative' : 'primary'"
                        :label="col.value"
                    />
                    <div v-else-if="col.name === 'progress'" class="flex items-center gap-1" style="min-width: 80px">
                        <q-linear-progress
                            :value="props.row.progress_pct"
                            :color="props.row.status === 'rejected' ? 'negative' : props.row.status === 'completed' ? 'positive' : 'primary'"
                            style="width: 50px; height: 6px"
                            rounded
                        />
                        <span class="text-caption">{{ col.value }}</span>
                    </div>
                    <span v-else>{{ col.value }}</span>
                </q-td>
            </q-tr>
            <q-tr v-show="props.expand" :props="props">
                <q-td colspan="100%">
                    <div class="q-pa-sm">
                        <div v-for="(ev, i) in props.row.events" :key="i" class="q-mb-xs">
                            <span class="text-caption text-grey q-mr-sm">{{ ev.ts }}</span>
                            <span class="text-caption">{{ ev.label }}</span>
                            <div v-if="ev.comment" class="text-caption text-grey q-ml-lg">{{ ev.comment }}</div>
                        </div>
                        <span v-if="!props.row.events.length" class="text-caption text-grey">—</span>
                    </div>
                </q-td>
            </q-tr>
        ''')

        # Filter row above table
        type_options = sorted({r["type"] for r in rows})
        status_options = sorted({r["status"] for r in rows})

        table.add_slot("top-left", "")
        with table.add_slot("top-right"):
            with ui.row().classes("items-center gap-2"):
                type_filter = ui.select(
                    options=[""] + type_options,
                    value="",
                    label=t("workflow_type"),
                ).props("outlined dense clearable").classes("min-w-[160px]")

                status_filter = ui.select(
                    options=[""] + status_options,
                    value="",
                    label=t("status"),
                ).props("outlined dense clearable").classes("min-w-[140px]")

                search = ui.input(placeholder="Search...").props("outlined dense clearable").classes("min-w-[160px]")

        def apply_filters():
            filtered = rows
            if type_filter.value:
                filtered = [r for r in filtered if r["type"] == type_filter.value]
            if status_filter.value:
                filtered = [r for r in filtered if r["status"] == status_filter.value]
            if search.value:
                q = search.value.lower()
                filtered = [r for r in filtered if q in r["target"].lower() or q in r["type"].lower()]
            table.rows = filtered

        type_filter.on_value_change(lambda _: apply_filters())
        status_filter.on_value_change(lambda _: apply_filters())
        search.on("update:model-value", lambda _: apply_filters())


# ---------------------------------------------------------------------------
# Awaiting My Action — grid of cards
# ---------------------------------------------------------------------------


async def _render_actionable(container, user: User):
    container.clear()
    requests = await list_actionable(user)

    with container:
        ui.label(t("awaiting_action")).classes("text-h6 mb-2 mt-4")
        if not requests:
            ui.label(t("no_pending")).classes("text-grey")
            return

        cfg = await workflows_config.get()
        wf_labels = await _workflow_labels()
        state = {"expanded_id": None}

        # Responsive grid: 1 col on small, 2 on medium, 3 on large
        with ui.element("div").classes(
            "w-full grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"
        ):
            for req in requests:
                wf = cfg.workflows.get(req.type)
                if not wf:
                    continue
                step_config = get_current_step_config(req, wf)
                if not step_config:
                    continue

                target = _target_display(req)
                group_label = wf_labels.get(req.type, req.type)
                current, total = get_step_progress(req, wf)

                with ui.card().classes("cursor-pointer q-py-sm q-px-md") as card:
                    with ui.row().classes("items-center justify-between w-full"):
                        with ui.column().classes("gap-0"):
                            ui.label(target or group_label).classes("font-bold")
                            ui.label(group_label).classes("text-xs text-grey")
                            ui.label(
                                f"{t('current_step')}: {step_config.key} ({current}/{total})"
                            ).classes("text-sm text-grey-8")
                        if req.updated_at:
                            ui.label(_format_date(req.updated_at)).classes("text-sm text-grey")
                    ui.linear_progress(
                        value=current / total if total else 0, color="primary",
                    ).classes("w-full").props("rounded size=6px")

                    action_container = ui.column().classes("w-full mt-2")
                    action_container.set_visibility(False)
                    action_container.on("click.stop", js_handler="() => {}")

                    async def _expand(
                        ac=action_container, r=req, sc=step_config, w=wf, st=state,
                    ):
                        if st["expanded_id"] == r.id:
                            ac.set_visibility(False)
                            st["expanded_id"] = None
                            return
                        st["expanded_id"] = r.id
                        ac.set_visibility(True)
                        ac.clear()
                        with ac:
                            ui.separator()
                            await _render_action_form(container, user, r, sc, w)

                    card.on("click", _expand)


async def _render_action_form(outer_container, user, req, step_config, wf):
    async def handle_approve(comment, r=req):
        try:
            await submit_step(r.id, user.id, "approve", comment=comment, actor_user=user)
        except Exception as e:
            ui.notify(str(e), color="negative")
            return
        ui.notify(t("step_submitted"), color="positive")
        await _render_actionable(outer_container, user)

    async def handle_reject(comment, r=req):
        try:
            await submit_step(r.id, user.id, "reject", comment=comment, actor_user=user)
        except Exception as e:
            ui.notify(str(e), color="negative")
            return
        ui.notify(t("step_submitted"), color="positive")
        await _render_actionable(outer_container, user)

    async def handle_submit(data, r=req):
        try:
            await submit_step(r.id, user.id, "submit", data=data, actor_user=user)
        except Exception as e:
            ui.notify(str(e), color="negative")
            return
        ui.notify(t("step_submitted"), color="positive")
        await _render_actionable(outer_container, user)

    if step_config.type == "approval":
        render_approval(req.data, wf, step_config, handle_approve, handle_reject)
    elif step_config.type == "form":
        await render_step_form(step_config, req.data, on_submit=handle_submit)
