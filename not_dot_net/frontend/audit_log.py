"""Audit log viewer — admin-only tab showing system events."""

from nicegui import ui

from not_dot_net.frontend import safe_timer
from not_dot_net.backend.audit import list_audit_events
from not_dot_net.frontend.i18n import t
from not_dot_net.backend.permissions import permission

VIEW_AUDIT_LOG = permission("view_audit_log", "View audit log", "Access the audit log")

CATEGORIES = ["auth", "workflow", "booking", "resource", "user"]


def render():
    container = ui.column().classes("w-full")

    async def refresh():
        await _render_log(container)

    safe_timer(0, refresh, once=True)


async def _render_log(container, category=None, actor_email=None):
    container.clear()

    events = await list_audit_events(
        category=category, actor_email=actor_email, limit=200,
    )

    with container:
        ui.label(t("audit_log")).classes("text-h6 mb-2")

        # Filters
        with ui.row().classes("items-end gap-2 mb-3"):
            cat_select = ui.select(
                options=[""] + CATEGORIES,
                value=category or "",
                label=t("category"),
            ).props("outlined dense clearable").classes("min-w-[150px]")

            email_input = ui.input(
                label=t("actor"),
                value=actor_email or "",
            ).props("outlined dense clearable").classes("min-w-[200px]")

            async def apply():
                c = cat_select.value or None
                e = email_input.value.strip() or None
                await _render_log(container, category=c, actor_email=e)

            ui.button(t("filter"), icon="search", on_click=apply).props(
                "flat color=primary"
            )

        if not events:
            ui.label(t("no_events")).classes("text-grey")
            return

        # Table
        columns = [
            {"name": "time", "label": t("time"), "field": "time", "sortable": True, "align": "left"},
            {"name": "category", "label": t("category"), "field": "category", "sortable": True, "align": "left"},
            {"name": "action", "label": t("action"), "field": "action", "sortable": True, "align": "left"},
            {"name": "actor", "label": t("actor"), "field": "actor", "sortable": True, "align": "left"},
            {"name": "target", "label": t("target"), "field": "target", "sortable": True, "align": "left"},
            {"name": "detail", "label": t("detail"), "field": "detail", "align": "left"},
        ]

        rows = [
            {
                "_id": str(ev.id),
                "time": ev.created_at.strftime("%Y-%m-%d %H:%M:%S") if ev.created_at else "",
                "category": ev.category,
                "action": ev.action,
                "actor": ev.actor_email or ev.actor_id or "—",
                "target": f"{ev.target_type}: {getattr(ev, '_target_display', ev.target_id)}" if ev.target_type and ev.target_id else "—",
                "detail": ev.detail or "",
            }
            for ev in events
        ]

        table = ui.table(
            columns=columns, rows=rows, row_key="_id",
            pagination={"rowsPerPage": 25},
        ).classes("w-full")
        table.props("flat bordered dense")

        # Color-code categories
        table.add_slot("body-cell-category", r'''
            <q-td :props="props">
                <q-badge
                    :color="
                        props.value === 'auth' ? 'orange' :
                        props.value === 'workflow' ? 'primary' :
                        props.value === 'booking' ? 'teal' :
                        props.value === 'resource' ? 'purple' :
                        'grey'
                    "
                    :label="props.value"
                />
            </q-td>
        ''')
