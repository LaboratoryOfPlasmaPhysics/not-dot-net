"""Dashboard tab — Awaiting My Action + My Requests."""

import re
from datetime import datetime, timedelta, timezone

from nicegui import ui


from not_dot_net.backend.db import User
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.workflow_config import workflows_config
from not_dot_net.backend.workflow_service import (
    list_user_requests,
    list_all_requests,
    list_actionable,
    list_events_batch,
    compute_step_age_days,
    resolve_actor_names,
)
from not_dot_net.backend.workflow_engine import get_current_step_config, get_step_progress
from not_dot_net.config import dashboard_config, step_display
from not_dot_net.frontend.i18n import format_short_date, t
from not_dot_net.frontend.workflow_step import (
    render_step_progress,
)


_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)(.+?)\1")


def _preview_line(content: str) -> str:
    """First meaningful line of page content, with inline Markdown stripped so
    previews read as plain text instead of showing raw **bold**/[link]() syntax."""
    line = next(
        (ln for ln in content.splitlines() if ln.strip() and not ln.startswith("#")),
        "",
    )
    line = _MD_LINK.sub(r"\1", line)
    while _MD_EMPHASIS.search(line):
        line = _MD_EMPHASIS.sub(r"\2", line)
    return line.strip()


def render(user: User):
    """Render the dashboard tab content. Returns the refresh coroutine."""
    pages_container = ui.column().classes("w-full")
    actionable_container = ui.column().classes("w-full")
    my_requests_container = ui.column().classes("w-full")

    async def refresh():
        await _render_pages_section(pages_container)
        if user.is_active:
            await _render_actionable(actionable_container, user)
            await _render_my_requests(my_requests_container, user)

    ui.timer(0, refresh, once=True)
    return refresh


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
                    first_line = _preview_line(page.content)
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


TIME_PERIOD_DAYS = {
    "last_7_days": 7,
    "last_30_days": 30,
    "last_90_days": 90,
    "last_year": 365,
    "all_time": None,
}


def _since_from_period(period_key: str) -> datetime | None:
    days = TIME_PERIOD_DAYS.get(period_key)
    if days is None:
        return None
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def _urgency_card_style(age: int, fresh_days: int, aging_days: int) -> str:
    if age < fresh_days:
        color = "76, 175, 80"
    elif age < aging_days:
        color = "245, 124, 0"
    else:
        color = "211, 47, 47"
    return (
        f"background: rgba({color}, 0.16); "
        f"border: 1px solid rgba({color}, 0.28);"
    )


def _render_actionable_urgency_badge(age: int) -> None:
    with ui.element("span").classes(
        "inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium"
    ).style(
        "color: #111827; border: 1px solid rgba(17, 24, 39, 0.35); "
        "border-radius: 999px; background: rgba(255, 255, 255, 0.35);"
    ):
        ui.icon("schedule", size="14px").style("color: #111827;")
        ui.label(f"{age}d").classes("leading-none").style("color: #111827;")




# ---------------------------------------------------------------------------
# My Requests — table view
# ---------------------------------------------------------------------------


def _requests_title_key(*, sees_all: bool) -> str:
    return "all_requests" if sees_all else "my_requests"


async def _render_my_requests(container, user: User):
    container.clear()
    is_admin = await has_permissions(user, "view_audit_log")

    period_options = {k: t(k) for k in TIME_PERIOD_DAYS}

    with container:
        with ui.row().classes("items-center justify-between w-full mb-2"):
            ui.label(t(_requests_title_key(sees_all=is_admin))).classes("text-h6")
            period_select = ui.select(
                options=period_options,
                value="last_30_days",
                label=t("time_period"),
            ).props("outlined dense").classes("min-w-[160px]")

        table_container = ui.column().classes("w-full")

    async def load_table():
        since = _since_from_period(period_select.value)
        if is_admin:
            requests = await list_all_requests(since=since)
        else:
            requests = await list_user_requests(user.id, since=since)
        await _render_requests_table(table_container, requests)

    period_select.on_value_change(lambda _: load_table())
    await load_table()


async def _render_requests_table(container, requests):
    container.clear()

    with container:
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
            step_label = step_display(step_config) if step_config else req.current_step
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
                        :color="col.value === 'completed' ? 'positive' : col.value === 'rejected' ? 'negative' : col.value === 'cancelled' ? 'grey' : 'primary'"
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
                            :color="props.row.status === 'rejected' ? 'negative' : props.row.status === 'completed' ? 'positive' : props.row.status === 'cancelled' ? 'grey' : 'primary'"
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

                search = ui.input(placeholder=t("search")).props("outlined dense clearable").classes("min-w-[160px]")

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
                ).style(
                    _urgency_card_style(
                        age,
                        dash_cfg.urgency_fresh_days,
                        dash_cfg.urgency_aging_days,
                    )
                ).on("click", lambda _, r=req: ui.navigate.to(f"/workflow/request/{r.id}")):
                    # Header: target + urgency
                    with ui.row().classes("items-center justify-between w-full"):
                        with ui.column().classes("gap-0"):
                            ui.label(target or group_label).classes("font-bold")
                            ui.label(group_label).classes("text-xs text-grey")
                        with ui.row().classes("items-center gap-1"):
                            if age >= dash_cfg.urgency_aging_days:
                                ui.icon("warning", size="xs").classes("text-negative")
                            _render_actionable_urgency_badge(age)

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
                            format_short_date(last_comment.created_at)
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
