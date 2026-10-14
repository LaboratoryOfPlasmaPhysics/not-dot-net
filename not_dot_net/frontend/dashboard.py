"""Dashboard tab — Awaiting My Action + My Requests."""

import asyncio
import re
from datetime import datetime, timedelta, timezone

from nicegui import ui


from not_dot_net.backend.db import User
from not_dot_net.backend.permissions import get_permissions, has_permissions
from not_dot_net.backend.workflow_config import workflows_config
from not_dot_net.backend.effect_retry import pending_effects
from not_dot_net.backend.workflow_models import RequestStatus
from not_dot_net.backend.workflow_service import (
    list_user_requests,
    list_all_requests,
    list_actionable,
    list_events_batch,
    compute_step_age_days,
    resolve_actor_names,
)
from not_dot_net.backend.roles import roles_config
from not_dot_net.backend.workflow_engine import effective_assignee, get_current_step_config, get_step_progress
from not_dot_net.config import dashboard_config, step_display
from not_dot_net.frontend.i18n import format_datetime, format_short_date, t
from not_dot_net.frontend.workflow_step import (
    event_label,
    request_display_name,
    render_step_progress,
)


_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)(.+?)\1")


def _preview_line(content: str) -> str:
    """First prose line of page content (headings and table rows skipped), with
    inline Markdown stripped so previews read as plain text instead of showing
    raw **bold**/[link]() syntax."""
    line = next(
        (ln for ln in content.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "|"))),
        "",
    )
    line = _MD_LINK.sub(r"\1", line)
    while _MD_EMPHASIS.search(line):
        line = _MD_EMPHASIS.sub(r"\2", line)
    return line.strip()


def render(user: User):
    """Render the dashboard tab content. Returns the refresh coroutine."""
    actionable_container = ui.column().classes("w-full")
    my_requests_container = ui.column().classes("w-full")
    pages_container = ui.column().classes("w-full mt-6")

    # Each section clears, awaits the DB, then draws: overlapping refreshes
    # (initial timer + shell tab refresh) would both draw — duplicated cards.
    lock = asyncio.Lock()

    async def refresh():
        async with lock:
            if user.is_active:
                await _render_actionable(actionable_container, user)
                await _render_my_requests(my_requests_container, user)
            await _render_pages_section(pages_container)

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
    return format_datetime(dt, "%Y-%m-%d")


_STATUS_COLOR = {
    RequestStatus.COMPLETED: "positive",
    RequestStatus.REJECTED: "negative",
    RequestStatus.CANCELLED: "grey",
}


def _status_badge(status: str, *, needs_action: bool) -> tuple[str, str]:
    """(label, colour) of the status badge. A completed request whose AD step
    failed is not done: the person is still missing their access."""
    if status == RequestStatus.COMPLETED and needs_action:
        return t("completed_action_needed"), "orange-9"
    return t(status), _STATUS_COLOR.get(status, "primary")


def _waiting_for(req, step, roles: dict, *, requester_name: str) -> str:
    """Who the request is blocked on, in words a person can chase."""
    if req.status != RequestStatus.IN_PROGRESS or step is None:
        return ""
    match effective_assignee(step):
        case ("target_person", _):
            return request_display_name(req)
        case ("requester", _):
            return requester_name
        case ("permission", perm):
            holders = ", ".join(r.label for r in roles.values() if perm in r.permissions)
            info = get_permissions().get(perm)
            return holders or (info.label if info else perm)
        case ("role", role):
            return roles[role].label if role in roles else role
    return ""


def _age_badge(age: int, status: str, dash_cfg) -> tuple[str, str]:
    """(label, colour) of the age badge; none on a closed request, where it reads as 'late'."""
    if status != RequestStatus.IN_PROGRESS:
        return "", ""
    if age < dash_cfg.urgency_fresh_days:
        color = "positive"
    elif age < dash_cfg.urgency_aging_days:
        color = "warning"
    else:
        color = "negative"
    return t("age_days", days=age), color


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
        ui.label(t("age_days", days=age)).classes("leading-none").style("color: #111827;")




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
        with ui.row().classes("items-center w-full mb-2 gap-3"):
            ui.label(t(_requests_title_key(sees_all=is_admin))).classes("text-h6")
            if await has_permissions(user, "create_workflows"):
                ui.button(
                    t("new_request"), icon="add_circle",
                    on_click=lambda: ui.navigate.to("/?tab=new_request"),
                ).props("color=primary")
            ui.space()
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
        needs_action = {e.request_id for e in await pending_effects()}
        roles = (await roles_config.get()).roles
        requester_names = await resolve_actor_names({r.created_by for r in requests if r.created_by})

        columns = [
            {"name": "type", "label": t("workflow_type"), "field": "type", "sortable": True, "align": "left"},
            {"name": "target", "label": t("target_person"), "field": "target", "sortable": True, "align": "left"},
            {"name": "progress", "label": t("progress"), "field": "progress", "sortable": True, "align": "center"},
            {"name": "step", "label": t("current_step"), "field": "step", "sortable": True, "align": "left"},
            {"name": "waiting_for", "label": t("waiting_for"), "field": "waiting_for", "sortable": True, "align": "left"},
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
            age_label, age_color = _age_badge(age, req.status, dash_cfg)
            status_label, status_color = _status_badge(req.status, needs_action=req.id in needs_action)

            rows.append({
                "id": str(req.id),
                "type": wf_labels.get(req.type, req.type),
                "target": request_display_name(req),
                "progress": f"{current}/{total}",
                "progress_pct": current / total if total else 0,
                "step": step_label,
                "waiting_for": _waiting_for(
                    req, step_config, roles, requester_name=requester_names.get(req.created_by, ""),
                ),
                "age": age,
                "age_label": age_label,
                "age_color": age_color,
                "date": _format_date(req.created_at),
                "status": req.status,
                "status_label": status_label,
                "status_color": status_color,
            })

        table = ui.table(
            columns=columns, rows=rows, row_key="id", pagination={"rowsPerPage": 15},
        ).classes("w-full")
        table.props("flat bordered dense")

        table.add_slot("body", r'''
            <q-tr :props="props" tabindex="0" class="cursor-pointer"
                  @click="() => $parent.$emit('row-click', props.row)"
                  @keydown.enter="() => $parent.$emit('row-click', props.row)">
                <q-td v-for="col in props.cols" :key="col.name" :props="props">
                    <q-badge v-if="col.name === 'status'"
                        :color="props.row.status_color"
                        :label="props.row.status_label"
                    />
                    <q-badge v-else-if="col.name === 'age' && props.row.age_label"
                        :color="props.row.age_color"
                        :label="props.row.age_label"
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
                    <span v-else-if="col.name !== 'age'">{{ col.value }}</span>
                </q-td>
            </q-tr>
        ''')

        table.on("row-click", lambda e: ui.navigate.to(f"/workflow/request/{e.args['id']}"))

        # Filter row
        type_options = sorted({r["type"] for r in rows})
        status_options = {r["status"]: r["status_label"] for r in rows}

        table.add_slot("top-left", "")
        with table.add_slot("top-right"):
            with ui.row().classes("items-center gap-2"):
                type_filter = ui.select(
                    options=[""] + type_options,
                    value="",
                    label=t("workflow_type"),
                ).props("outlined dense clearable").classes("min-w-[160px]")

                status_filter = ui.select(
                    options={"": "", **dict(sorted(status_options.items(), key=lambda kv: kv[1]))},
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
                target = request_display_name(req)
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
                            ui.label(f"{actor} — {event_label(wf, last_event.step_key, last_event.action)}").classes(
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
