"""Reusable step renderer — form fields or approval UI."""

import asyncio
import logging
from datetime import date as dt_date

import httpx
from nicegui import ui

from not_dot_net.backend.workflow_engine import get_completion_status
from not_dot_net.config import WorkflowStepConfig
from not_dot_net.frontend.i18n import TRANSLATIONS, get_locale, t

_log = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_HEADERS = {"User-Agent": "LPP-Intranet/1.0"}


async def _nominatim_search(query: str) -> list[dict]:
    """Returns list of {display_name, lat, lon}."""
    if len(query) < 3:
        return []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                _NOMINATIM_URL,
                params={"q": query, "format": "json", "limit": 5},
                headers=_NOMINATIM_HEADERS,
                timeout=5,
            )
            if resp.status_code != 200:
                return []
            return [
                {"display_name": r["display_name"], "lat": float(r["lat"]), "lon": float(r["lon"])}
                for r in resp.json()
            ]
    except Exception:
        _log.exception("Nominatim search failed")
        return []


async def _render_field(field_cfg, data, fields, files, on_file_upload, max_upload_size_mb, width_class):
    label = t(field_cfg.label) if field_cfg.label else field_cfg.name
    value = data.get(field_cfg.name, "")

    if field_cfg.type == "textarea":
        fields[field_cfg.name] = ui.textarea(
            label=label, value=value
        ).props("outlined dense").classes(width_class)
    elif field_cfg.type == "date":
        with ui.input(label=label, value=value).props("outlined dense").classes(width_class) as inp:
            with ui.menu().props("no-parent-event") as menu:
                with ui.date(on_change=lambda e, i=inp, m=menu: _set_date(i, m, e)):
                    pass
            with inp.add_slot("append"):
                ui.icon("edit_calendar").on("click", menu.open).classes("cursor-pointer")
        fields[field_cfg.name] = inp
    elif field_cfg.type == "select":
        options = await _resolve_options(field_cfg.options_key)
        fields[field_cfg.name] = ui.select(
            label=label, options=options, value=value if value in options else None
        ).props("outlined dense stack-label").classes(width_class)
    elif field_cfg.type == "file":
        uploaded = (files or {}).get(field_cfg.name)
        if uploaded:
            with ui.row().classes(f"{width_class} items-center gap-2"):
                ui.icon("check_circle", color="positive", size="sm")
                ui.label(f"{label}: {uploaded}").classes("text-positive text-sm")
        elif on_file_upload:
            req_mark = " *" if field_cfg.required else ""
            ui.upload(
                label=f"{label}{req_mark}",
                auto_upload=True,
                max_file_size=max_upload_size_mb * 1024 * 1024,
                on_upload=lambda e, name=field_cfg.name: on_file_upload(name, e),
            ).props("outlined flat accept='.pdf,.jpg,.jpeg,.png,.doc,.docx'").classes(width_class)
        else:
            ui.label(f"{label}: no upload available").classes("text-grey text-sm")
        fields[field_cfg.name] = None
    elif field_cfg.type == "location":
        nominatim_results: list[dict] = []
        loc_select = ui.select(
            label=label,
            options={value: value} if value else {},
            value=value or None,
            with_input=True,
        ).props("outlined dense stack-label use-input hide-selected fill-input input-debounce=500").classes("w-full")
        loc_map = ui.leaflet(center=(48.71, 2.21), zoom=4).classes("w-full rounded").style("height: 200px")
        loc_marker = None

        async def _on_input_value(e, sel=loc_select):
            nonlocal nominatim_results
            query = e.args if isinstance(e.args, str) else ""
            if len(query) < 3:
                return
            nominatim_results = await _nominatim_search(query)
            sel.set_options({r["display_name"]: r["display_name"] for r in nominatim_results})

        def _on_select_change(e, sel=loc_select):
            nonlocal loc_marker
            chosen = sel.value
            if not chosen:
                return
            match = next((r for r in nominatim_results if r["display_name"] == chosen), None)
            if match:
                coords = (match["lat"], match["lon"])
                loc_map.set_center(coords)
                loc_map.set_zoom(12)
                if loc_marker is not None:
                    loc_marker.move(coords[0], coords[1])
                else:
                    loc_marker = loc_map.marker(latlng=coords)

        loc_select.on("input-value", lambda e: asyncio.ensure_future(_on_input_value(e)))
        loc_select.on("update:model-value", _on_select_change)
        fields[field_cfg.name] = loc_select
    elif field_cfg.type == "email":
        fields[field_cfg.name] = ui.input(
            label=label, value=value, validation={t("invalid_email"): lambda v: "@" in v if v else True}
        ).props("outlined dense type=email").classes(width_class)
    else:
        fields[field_cfg.name] = ui.input(
            label=label, value=value
        ).props("outlined dense").classes(width_class)


async def render_step_form(
    step: WorkflowStepConfig,
    data: dict,
    on_submit,
    on_save_draft=None,
    files: dict | None = None,
    on_file_upload=None,
    max_upload_size_mb: int = 10,
):
    """Render a form step's fields. Returns dict of field name -> ui element."""
    fields = {}

    # Group consecutive half_width fields into pairs for row layout
    groups: list[list] = []
    for field_cfg in step.fields:
        if field_cfg.half_width:
            if groups and isinstance(groups[-1], list) and len(groups[-1]) < 2 and groups[-1][0].half_width:
                groups[-1].append(field_cfg)
            else:
                groups.append([field_cfg])
        else:
            groups.append([field_cfg])

    for group in groups:
        is_pair = len(group) == 2 and group[0].half_width
        if is_pair:
            with ui.row().classes("w-full gap-4"):
                for field_cfg in group:
                    await _render_field(field_cfg, data, fields, files, on_file_upload, max_upload_size_mb, "flex-1 min-w-[200px]")
        else:
            await _render_field(group[0], data, fields, files, on_file_upload, max_upload_size_mb, "w-full")

    # Date-pair: show duration when both departure_date and return_date are set
    if "departure_date" in fields and "return_date" in fields:
        _wire_date_pair(fields["departure_date"], fields["return_date"])

    # Completion status for partial-save steps
    if step.partial_save:
        _render_completion_indicator(step, data, files or {})

    with ui.row().classes("mt-4 gap-2"):
        if on_save_draft and step.partial_save:
            ui.button(t("save_draft"), on_click=lambda: on_save_draft(_collect_data(fields))).props(
                "flat"
            )

        async def validated_submit():
            collected = _collect_data(fields)
            missing = [
                t(f.label) if f.label else f.name for f in step.fields
                if f.required and f.type != "file" and not collected.get(f.name)
            ]
            if missing:
                ui.notify(f"{t('required_field')}: {', '.join(missing)}", color="negative")
                return
            error = _validate_date_pair(collected)
            if error:
                ui.notify(error, color="negative")
                return
            await on_submit(collected)

        ui.button(t("submit"), on_click=validated_submit).props("color=primary")

    return fields


def _render_completion_indicator(step: WorkflowStepConfig, data: dict, files: dict):
    """Show which required fields are filled for partial-save steps."""
    required = [f for f in step.fields if f.required]
    if not required:
        return
    filled = sum(
        1 for f in required
        if (f.type == "file" and files.get(f.name)) or (f.type != "file" and data.get(f.name))
    )
    ui.linear_progress(value=filled / len(required)).classes("w-full mb-2")
    ui.label(f"{filled}/{len(required)}").classes("text-sm text-grey")


def render_approval(
    request_data: dict,
    workflow,
    step: WorkflowStepConfig,
    on_approve,
    on_reject,
    on_request_corrections=None,
):
    """Render approval view: read-only data + approve/reject/corrections."""
    ui.label(workflow.label).classes("text-h6")

    for key, value in request_data.items():
        if value:
            label = t(key) if key in TRANSLATIONS.get(get_locale(), {}) else key
            ui.label(f"{label}: {value}").classes("text-sm")

    comment_input = ui.textarea(label=t("comment")).props("outlined dense").classes("w-full mt-2")

    with ui.row().classes("mt-4 gap-2"):
        ui.button(
            t("approve"),
            icon="check",
            on_click=lambda: on_approve(comment_input.value),
        ).props("color=positive")
        if on_request_corrections:
            ui.button(
                t("request_corrections"),
                icon="edit_note",
                on_click=lambda: on_request_corrections(comment_input.value),
            ).props("color=warning")
        ui.button(
            t("reject"),
            icon="close",
            on_click=lambda: on_reject(comment_input.value),
        ).props("color=negative")


def render_status_badge(status: str):
    """Render a colored status badge."""
    colors = {
        "in_progress": "primary",
        "completed": "positive",
        "rejected": "negative",
        "cancelled": "grey",
    }
    color = colors.get(status, "grey")
    ui.badge(t(status), color=color)


def render_urgency_badge(age_days: int, fresh_days: int = 2, aging_days: int = 7):
    """Render a colored urgency badge based on step age."""
    if age_days < fresh_days:
        color = "positive"
    elif age_days < aging_days:
        color = "warning"
    else:
        color = "negative"
    ui.badge(f"⏱ {age_days}d", color=color).props("outline")


def render_step_progress(current_step: str, status: str, steps: list):
    """Render a named step progress bar.

    Args:
        current_step: key of the current step
        status: request status (in_progress, completed, rejected)
        steps: list of WorkflowStepConfig from the workflow
    """
    step_keys = [s.key for s in steps]
    current_idx = step_keys.index(current_step) if current_step in step_keys else 0
    is_completed = status == "completed"

    with ui.row().classes("w-full gap-1 items-center"):
        for i, step in enumerate(steps):
            if is_completed or i < current_idx:
                color = "bg-positive"
            elif i == current_idx:
                color = "bg-primary"
            else:
                color = "bg-grey-4"
            height = "h-[6px]" if i == current_idx and not is_completed else "h-[4px]"
            ui.element("div").classes(f"flex-1 rounded {color} {height}")

    with ui.row().classes("w-full gap-1"):
        for i, step in enumerate(steps):
            if is_completed or i < current_idx:
                label = f"✓ {step.key}"
                cls = "text-[11px] text-grey flex-1"
            elif i == current_idx:
                label = f"● {step.key}"
                cls = "text-[11px] text-primary font-semibold flex-1"
            else:
                label = step.key
                cls = "text-[11px] text-grey-4 flex-1"
            ui.label(label).classes(cls)


def _parse_date(value: str) -> dt_date | None:
    try:
        return dt_date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _wire_date_pair(departure_input, return_input):
    """Add a duration badge between departure and return date fields."""
    duration_label = ui.label().classes("text-sm text-primary")

    def update_duration(*_args):
        dep = _parse_date(departure_input.value)
        ret = _parse_date(return_input.value)
        if dep and ret and ret >= dep:
            days = (ret - dep).days
            nights = max(0, days)
            duration_label.text = t("mission_duration", days=days + 1, nights=nights)
            duration_label.set_visibility(True)
        else:
            duration_label.set_visibility(False)

    departure_input.on("update:model-value", update_duration)
    return_input.on("update:model-value", update_duration)
    update_duration()


def _validate_date_pair(collected: dict) -> str | None:
    dep_str = collected.get("departure_date")
    ret_str = collected.get("return_date")
    if not dep_str or not ret_str:
        return None
    dep = _parse_date(dep_str)
    ret = _parse_date(ret_str)
    if not dep or not ret:
        return None
    if dep < dt_date.today():
        return t("departure_in_past")
    if ret < dep:
        return t("return_before_departure")
    return None


def _collect_data(fields: dict) -> dict:
    """Collect values from UI fields."""
    return {
        name: (el.value if el is not None else None)
        for name, el in fields.items()
        if el is not None
    }


def _set_date(inp, menu, event):
    inp.value = event.value
    menu.close()


async def _resolve_options(options_key: str | None) -> list[str]:
    """Resolve select field options from config."""
    if not options_key:
        return []
    if options_key == "teams":
        from not_dot_net.config import org_config
        cfg = await org_config.get()
        return cfg.teams
    if options_key == "roles":
        from not_dot_net.backend.roles import roles_config
        cfg = await roles_config.get()
        return list(cfg.roles.keys())
    if options_key == "employment_statuses":
        from not_dot_net.config import org_config
        cfg = await org_config.get()
        return cfg.employment_statuses
    if options_key == "employers":
        from not_dot_net.config import org_config
        cfg = await org_config.get()
        return cfg.employers
    if options_key == "transport_modes":
        from not_dot_net.config import org_config
        cfg = await org_config.get()
        return cfg.transport_modes
    if options_key == "funding_sources":
        from not_dot_net.config import org_config
        cfg = await org_config.get()
        return cfg.funding_sources
    return []
