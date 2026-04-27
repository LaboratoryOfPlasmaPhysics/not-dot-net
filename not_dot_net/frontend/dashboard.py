"""Dashboard tab — My Requests + Awaiting My Action."""

from nicegui import ui

from not_dot_net.frontend import safe_timer

from not_dot_net.backend.db import User
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.workflow_service import (
    list_user_requests,
    list_all_requests,
    list_actionable,
    list_events_batch,
    compute_step_age_days,
    resolve_actor_names,
    workflows_config,
)
from not_dot_net.backend.workflow_engine import get_current_step_config, get_step_progress
from not_dot_net.config import dashboard_config
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.workflow_step import (
    render_status_badge,
    render_step_progress,
    render_urgency_badge,
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

    safe_timer(0, refresh, once=True)


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
        dash_cfg = await dashboard_config.get()
        events_by_req = await list_events_batch([req.id for req in requests])

        columns = [
            {"name": "type", "label": t("workflow_type"), "field": "type", "sortable": True, "align": "left"},
            {"name": "target", "label": t("target_person"), "field": "target", "sortable": True, "align": "left"},
            {"name": "progress", "label": t("progress"), "field": "progress", "sortable": True, "align": "center"},
            {"name": "step", "label": t("current_step"), "field": "step", "sortable": True, "align": "left"},
            {"name": "age", "label": t("age"), "field": "age", "sortable": True, "align": "center"},
            {"name": "date", "label": t("created_at"), "field": "date", "sortable": True, "align": "left"},
            {"name": "status", "label": t("status"), "field": "status", "sortable": True, "align": "center"},
        ]

        rows = []
        for req in requests:
            wf = cfg.workflows.get(req.type)
            step_config = get_current_step_config(req, wf) if wf else None
            step_label = step_config.key if step_config else req.current_step
            current, total = get_step_progress(req, wf) if wf else (0, 0)
            events = events_by_req.get(req.id, [])
            age = compute_step_age_days(events, req.current_step)

            rows.append({
                "id": str(req.id),
                "type": wf_labels.get(req.type, req.type),
                "target": _target_display(req),
                "progress": f"{current}/{total}",
                "progress_pct": current / total if total else 0,
                "step": step_label,
                "age": age,
                "age_color": (
                    "positive" if age < dash_cfg.urgency_fresh_days
                    else "warning" if age < dash_cfg.urgency_aging_days
                    else "negative"
                ),
                "date": _format_date(req.created_at),
                "status": req.status,
            })

        table = ui.table(
            columns=columns, rows=rows, row_key="id", pagination={"rowsPerPage": 15},
        ).classes("w-full")
        table.props("flat bordered dense")

        table.add_slot("body", r'''
            <q-tr :props="props" @click="() => $parent.$emit('row-click', props.row)" class="cursor-pointer">
                <q-td v-for="col in props.cols" :key="col.name" :props="props">
                    <q-badge v-if="col.name === 'status'"
                        :color="col.value === 'completed' ? 'positive' : col.value === 'rejected' ? 'negative' : 'primary'"
                        :label="col.value"
                    />
                    <q-badge v-else-if="col.name === 'age'"
                        :color="props.row.age_color"
                        :label="col.value + 'd'"
                        outline
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
        ''')

        table.on("row-click", lambda e: ui.navigate.to(f"/workflow/request/{e.args['id']}"))

        # Filter row
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
# Awaiting My Action — enriched cards
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
        dash_cfg = await dashboard_config.get()

        events_by_req = await list_events_batch([req.id for req in requests])

        # Build card data and sort by age (oldest first)
        card_data = []
        for req in requests:
            wf = cfg.workflows.get(req.type)
            if not wf:
                continue
            step_config = get_current_step_config(req, wf)
            if not step_config:
                continue
            events = events_by_req.get(req.id, [])
            age = compute_step_age_days(events, req.current_step)
            card_data.append((req, wf, step_config, events, age))

        card_data.sort(key=lambda x: x[4], reverse=True)

        # Resolve all actor names in one batch
        all_actor_ids = set()
        for _, _, _, events, _ in card_data:
            all_actor_ids.update(ev.actor_id for ev in events if ev.actor_id)
        for req, _, _, _, _ in card_data:
            if req.created_by:
                all_actor_ids.add(req.created_by)
        actor_names = await resolve_actor_names(all_actor_ids)

        with ui.element("div").classes(
            "w-full grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"
        ):
            for req, wf, step_config, events, age in card_data:
                target = _target_display(req)
                group_label = wf_labels.get(req.type, req.type)
                requester = actor_names.get(req.created_by, "")

                last_event = next(
                    (ev for ev in reversed(events) if ev.action != "create"),
                    None,
                )
                last_comment = next(
                    (ev for ev in reversed(events) if ev.comment),
                    None,
                )

                with ui.card().classes(
                    "cursor-pointer q-py-sm q-px-md hover:shadow-lg transition-shadow"
                ).on("click", lambda _, r=req: ui.navigate.to(f"/workflow/request/{r.id}")):
                    # Header: target + urgency
                    with ui.row().classes("items-center justify-between w-full"):
                        with ui.column().classes("gap-0"):
                            ui.label(target or group_label).classes("font-bold")
                            ui.label(group_label).classes("text-xs text-grey")
                        render_urgency_badge(
                            age, dash_cfg.urgency_fresh_days, dash_cfg.urgency_aging_days,
                        )

                    # Step progress
                    render_step_progress(req.current_step, req.status, wf.steps)

                    # People
                    with ui.column().classes("gap-0 mt-2"):
                        ui.label(f"{t('requested_by')}: {requester}").classes(
                            "text-xs text-grey-8"
                        )
                        if last_event:
                            actor = actor_names.get(last_event.actor_id, t("via_token"))
                            ui.label(f"{actor} — {last_event.step_key}: {last_event.action}").classes(
                                "text-xs text-grey-8"
                            )

                    # Last comment
                    if last_comment:
                        actor = actor_names.get(last_comment.actor_id, "")
                        date_str = (
                            last_comment.created_at.strftime("%b %d")
                            if last_comment.created_at else ""
                        )
                        with ui.element("div").classes("mt-2 pl-3").style(
                            "border-left: 3px solid #1976d2; background: #f5f5f5; "
                            "padding: 4px 8px; border-radius: 4px;"
                        ):
                            comment_text = last_comment.comment
                            if len(comment_text) > 80:
                                comment_text = comment_text[:77] + "..."
                            ui.label(
                                f'💬 "{comment_text}" — {actor}, {date_str}'
                            ).classes("text-xs text-grey-8")
