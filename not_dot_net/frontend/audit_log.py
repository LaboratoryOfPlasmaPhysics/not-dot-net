"""Audit log viewer — admin-only tab showing system events."""

import json
from datetime import datetime, timedelta, timezone
from html import escape

from nicegui import ui

from not_dot_net.backend.audit import AuditEventView, list_audit_events
from not_dot_net.frontend.i18n import t
from not_dot_net.backend.permissions import permission

VIEW_AUDIT_LOG = permission("view_audit_log", "View audit log", "Access the audit log")

CATEGORIES = ["auth", "workflow", "booking", "resource", "user", "settings", "personal_data"]
FILTER_CONTROL_HEIGHT = "40px"

SEVERITY_ORDER = ("green", "orange", "red")

SEVERITY_ROW_COLOR = {
    "green": "#DCEFE2",
    "orange": "#FFE6B8",
    "red": "#FFB3B3",
}

SEVERITY_LABEL_KEY = {
    "green": "audit_severity_normal",
    "orange": "audit_severity_sensitive",
    "red": "audit_severity_critical",
}

# Severity drives off (category, action) and metadata_json, not the free-form
# detail string. Settings update/reset is the current exception because the
# section still comes through as detail (`section={prefix}`), matched exactly.
_CRITICAL_SETTINGS_SECTIONS = {"section=roles", "section=ldap"}

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


def _audit_severity(ev) -> str:
    """Classify an audit event into a severity color."""
    cat, action = ev.category, ev.action
    meta = ev.metadata_json or {}

    if cat == "auth" and action == "login":
        if meta.get("success") is False:
            return "red"
        if meta.get("is_superuser"):
            return "red"
        return "green"

    if cat == "settings":
        if action == "update_role":
            return "red"
        if action in {"update", "reset"}:
            if (ev.detail or "") in _CRITICAL_SETTINGS_SECTIONS:
                return "red"
            return "orange"
        if action in {"export", "import", "ldap_sync"}:
            return "orange"

    if cat == "personal_data" and action == "download":
        return "orange"

    if cat == "workflow" and action == "resend_notification":
        return "orange"

    if cat == "user":
        if action in {"add_tenure", "update_tenure", "delete_tenure"}:
            return "orange"
        if action == "update" and "role" in (meta.get("changes") or {}):
            return "red"

    return "green"


def _relative_time_label(dt: datetime | None, *, now: datetime | None = None) -> str:
    """Compact today/yesterday/before-yesterday/N-days-ago label."""
    if dt is None:
        return ""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    days = (now.date() - dt.date()).days
    hour = dt.strftime("%H:%M")
    if days <= 0:
        return hour
    if days == 1:
        return t("audit_time_yesterday", time=hour)
    if days == 2:
        return t("audit_time_before_yesterday", time=hour)
    return t("audit_time_days_ago", days=days)


def _severity_filter_options() -> dict:
    options = {"": t("audit_severity_all")}
    for sev in SEVERITY_ORDER:
        options[sev] = t(SEVERITY_LABEL_KEY[sev])
    return options



def normalize_filter(value: str | None) -> str | None:
    """Trim a clearable filter input to a value or None.

    Quasar emits None when a `clearable` input is cleared, which made the
    Filter button raise AttributeError on `.strip()`.
    """
    return (value or "").strip() or None


def render():
    container = ui.column().classes("w-full")

    async def refresh():
        await _render_log(container)

    ui.timer(0, refresh, once=True)
    return refresh


def _format_full(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _format_metadata(metadata: dict | None) -> str:
    if not metadata:
        return ""
    return json.dumps(metadata, ensure_ascii=False, sort_keys=True)


def _flatten_metadata(metadata: dict | None, prefix: str = "") -> list[tuple[str, str]]:
    if not metadata:
        return []

    items = []
    for key, value in metadata.items():
        label = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            items.extend(_flatten_metadata(value, label))
        elif isinstance(value, list):
            items.append((label, json.dumps(value, ensure_ascii=False)))
        else:
            items.append((label, str(value)))
    return items


def _metadata_detail_html(metadata: dict | None) -> str:
    items = _flatten_metadata(metadata)
    if not items:
        return '<div class="text-grey">—</div>'

    rows = []
    for key, value in items:
        rows.append(
            '<div style="display: flex; flex-wrap: wrap; gap: 2px 8px; padding: 5px 0; border-bottom: 1px solid rgba(0,0,0,.06);">'
            f'<div style="font-weight: 600; color: #455a64; min-width: 120px; max-width: 100%; overflow-wrap: anywhere;">{escape(key)}</div>'
            f'<div style="flex: 1 1 140px; min-width: 0; overflow-wrap: anywhere;">{escape(value)}</div>'
            '</div>'
        )
    return "".join(rows)


def _detail_field_html(label: str, value: str | None) -> str:
    display = value if value else "—"
    return (
        '<div style="margin-bottom: 10px;">'
        f'<div style="font-size: 11px; text-transform: uppercase; color: #607d8b; letter-spacing: .03em;">{escape(label)}</div>'
        f'<div style="word-break: break-word;">{escape(display)}</div>'
        '</div>'
    )


def _log_detail_html(row: dict) -> str:
    severity = row.get("severity", "green")
    severity_label = t(SEVERITY_LABEL_KEY.get(severity, SEVERITY_LABEL_KEY["green"]))
    severity_color = SEVERITY_ROW_COLOR.get(severity, SEVERITY_ROW_COLOR["green"])
    metadata = row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else None
    return (
        '<div style="font-size: 13px; line-height: 1.35;">'
        f'<div style="display: inline-block; padding: 4px 8px; margin-bottom: 12px; border-radius: 6px; '
        f'background: {severity_color}; border: 1px solid rgba(0,0,0,.12);">{escape(severity_label)}</div>'
        f'{_detail_field_html(t("time"), row.get("time_full"))}'
        f'{_detail_field_html(t("category"), row.get("category"))}'
        f'{_detail_field_html(t("action"), row.get("action"))}'
        f'{_detail_field_html(t("actor"), row.get("actor"))}'
        f'{_detail_field_html(t("target"), row.get("target"))}'
        f'{_detail_field_html(t("detail"), row.get("detail"))}'
        f'<div style="font-size: 11px; text-transform: uppercase; color: #607d8b; letter-spacing: .03em; margin-top: 12px;">{escape(t("metadata"))}</div>'
        f'<div style="margin-top: 6px;">{_metadata_detail_html(metadata)}</div>'
        '</div>'
    )


def _build_row(ev: AuditEventView, severity: str, relative_time: bool) -> dict:
    full = _format_full(ev.created_at)
    relative = _relative_time_label(ev.created_at)
    return {
        "_id": str(ev.id),
        "time": relative if relative_time else full,
        "time_full": full,
        "time_relative": relative,
        "category": ev.category,
        "action": ev.action,
        "actor": ev.actor_display or "—",
        "target": f"{ev.target_type}: {ev.target_display}" if ev.target_type and ev.target_display else "—",
        "detail": ev.detail or "",
        "metadata_json": ev.metadata_json or {},
        "severity": severity,
        "severity_row": SEVERITY_ROW_COLOR[severity],
    }


async def _render_log(
    container,
    category=None,
    actor_email=None,
    period="last_30_days",
    relative_time=False,
    severity_filter="",
):
    container.clear()

    since = _since_from_period(period)
    events = await list_audit_events(
        category=category, actor_email=actor_email, since=since, limit=500,
    )

    period_options = {k: t(k) for k in TIME_PERIOD_DAYS}
    relative_state = {"enabled": relative_time}
    severities = [_audit_severity(ev) for ev in events]
    severity_counts = {key: 0 for key in SEVERITY_ORDER}
    for sev in severities:
        severity_counts[sev] += 1

    with container:
        ui.label(t("audit_log")).classes("text-h6 mb-2")

        with ui.row().classes("items-end gap-2 mb-3"):
            period_select = ui.select(
                options=period_options,
                value=period,
                label=t("time_period"),
            ).props("outlined dense").classes("min-w-[160px]")

            cat_select = ui.select(
                options=[""] + CATEGORIES,
                value=category or "",
                label=t("category"),
            ).props("outlined dense clearable").classes("min-w-[150px]")

            email_input = ui.input(
                label=t("actor"),
                value=actor_email or "",
            ).props("outlined dense clearable").classes("min-w-[200px]")

            severity_select = ui.select(
                options=_severity_filter_options(),
                value=severity_filter,
                label=t("audit_severity"),
            ).props("outlined dense").classes("min-w-[140px]")

            async def apply():
                c = cat_select.value or None
                e = normalize_filter(email_input.value)
                p = period_select.value
                s = severity_select.value or ""
                await _render_log(
                    container,
                    category=c,
                    actor_email=e,
                    period=p,
                    relative_time=relative_state["enabled"],
                    severity_filter=s,
                )

            ui.button(t("filter"), icon="search", on_click=apply).props(
                "flat color=primary"
            )

            def _time_button_tooltip() -> str:
                return t("audit_time_full") if relative_state["enabled"] else t("audit_time_relative")

            async def toggle_time_display():
                relative_state["enabled"] = not relative_state["enabled"]
                for row in rows:
                    row["time"] = (
                        row["time_relative"]
                        if relative_state["enabled"]
                        else row["time_full"]
                    )
                table.rows = rows
                table.update()
                time_button.props(
                    f"flat round dense color={'primary' if relative_state['enabled'] else 'grey'}"
                )
                time_button.tooltip(_time_button_tooltip())

            with ui.element("div").style(
                f"height: {FILTER_CONTROL_HEIGHT}; display: flex; align-items: center;"
            ):
                time_button = ui.button(
                    icon="schedule",
                    on_click=toggle_time_display,
                ).props(
                    f"flat round dense color={'primary' if relative_state['enabled'] else 'grey'}"
                ).tooltip(_time_button_tooltip())

            with ui.element("div").style(
                f"height: {FILTER_CONTROL_HEIGHT}; display: flex; align-items: center;"
            ):
                with ui.row().classes("items-center gap-1"):
                    for severity in SEVERITY_ORDER:
                        ui.label(
                            f"{t(SEVERITY_LABEL_KEY[severity])} {severity_counts[severity]}"
                        ).classes("px-2 py-1 text-caption rounded-borders").style(
                            "background-color: "
                            f"{SEVERITY_ROW_COLOR[severity]}; color: #263238; "
                            "border: 1px solid rgba(0, 0, 0, 0.14); "
                            "line-height: 1.2;"
                        )

        if not events:
            ui.label(t("no_events")).classes("text-grey")
            return

        columns = [
            {"name": "time", "label": t("time"), "field": "time", "sortable": True, "align": "left"},
            {"name": "category", "label": t("category"), "field": "category", "sortable": True, "align": "left"},
            {"name": "action", "label": t("action"), "field": "action", "sortable": True, "align": "left"},
            {"name": "actor", "label": t("actor"), "field": "actor", "sortable": True, "align": "left"},
            {"name": "target", "label": t("target"), "field": "target", "sortable": True, "align": "left"},
            {"name": "detail", "label": t("detail"), "field": "detail", "align": "left"},
        ]

        rows = [
            _build_row(ev, sev, relative_time)
            for ev, sev in zip(events, severities)
            if not severity_filter or sev == severity_filter
        ]

        if not rows:
            ui.label(t("no_events")).classes("text-grey")
            return

        with ui.row().classes("w-full items-start gap-3"):
            with ui.element("div").classes("flex-1 min-w-0"):
                table = ui.table(
                    columns=columns, rows=rows, row_key="_id",
                    pagination={"rowsPerPage": 25},
                ).classes("w-full")
                table.props("flat bordered dense")

            with ui.card() as detail_panel:
                detail_panel.classes("w-1/4 min-w-[300px] max-w-[440px] q-pa-md sticky top-4")
                ui.label(t("audit_log")).classes("text-subtitle2 text-grey-7")
                detail_content = ui.html(
                    f'<div class="text-grey">{escape(t("select_audit_log"))}</div>'
                ).classes("w-full")
                detail_panel.set_visibility(False)

        def show_log_detail(e):
            row = e.args
            if not isinstance(row, dict):
                return
            detail_content.set_content(_log_detail_html(row))
            detail_panel.set_visibility(True)

        table.on("row-click", show_log_detail)

        # This `body` slot replaces the full row template. Any new column added
        # above must also be rendered below.
        table.add_slot("body", r'''
            <q-tr
                :props="props"
                :style="{ backgroundColor: props.row.severity_row }"
                class="cursor-pointer"
                @click="() => $parent.$emit('row-click', props.row)"
            >
                <q-td key="time" :props="props">{{ props.row.time }}</q-td>
                <q-td key="category" :props="props">
                    <q-badge
                        :color="
                            props.row.category === 'auth' ? 'orange' :
                            props.row.category === 'workflow' ? 'primary' :
                            props.row.category === 'booking' ? 'teal' :
                            props.row.category === 'resource' ? 'purple' :
                            'grey'
                        "
                        :label="props.row.category"
                    />
                </q-td>
                <q-td key="action" :props="props">{{ props.row.action }}</q-td>
                <q-td key="actor" :props="props">{{ props.row.actor }}</q-td>
                <q-td key="target" :props="props">{{ props.row.target }}</q-td>
                <q-td key="detail" :props="props">{{ props.row.detail }}</q-td>
            </q-tr>
        ''')
