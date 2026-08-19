"""Reusable step renderer — form fields or approval UI."""

import asyncio
import logging
from datetime import date as dt_date

import httpx
from nicegui import ui

from not_dot_net.backend.field_definitions import field_definitions_config, resolve_step_fields
from not_dot_net.backend.vocabularies import resolve_terms, term_label
from not_dot_net.config import WorkflowStepConfig, step_display
from not_dot_net.frontend.i18n import TRANSLATIONS, get_locale, t

_log = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
_NOMINATIM_HEADERS = {"User-Agent": "LPP-Intranet/1.0"}
_NOMINATIM_REVERSE_LOCALE = "en"


def _nominatim_headers(locale: str | None = None) -> dict[str, str]:
    headers = dict(_NOMINATIM_HEADERS)
    if locale:
        headers["Accept-Language"] = locale
    return headers


async def _nominatim_search(query: str) -> list[dict]:
    """Returns list of {display_name, lat, lon}."""
    if len(query) < 3:
        return []
    locale = get_locale()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                _NOMINATIM_URL,
                params={"q": query, "format": "json", "limit": 5, "accept-language": locale},
                headers=_nominatim_headers(locale),
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


def _format_reverse_location(payload: dict) -> str:
    address = payload.get("address") if isinstance(payload, dict) else None
    if not isinstance(address, dict):
        return payload.get("display_name", "") if isinstance(payload, dict) else ""
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("municipality")
        or address.get("county")
    )
    display_name = payload.get("display_name", "") if isinstance(payload, dict) else ""
    if display_name and city:
        parts = [part.strip() for part in display_name.split(",") if part.strip()]
        city_index = next((i for i, part in enumerate(parts) if part == city), None)
        if city_index is not None:
            return ", ".join(parts[city_index:])

    location_parts = [
        city,
        address.get("county"),
        address.get("state"),
        address.get("region"),
        address.get("postcode"),
        address.get("country"),
    ]
    compact = []
    for part in location_parts:
        if part and part not in compact:
            compact.append(part)
    return ", ".join(compact) or display_name


async def _nominatim_reverse(lat: float, lon: float) -> str:
    """Return a compact location label for map clicks."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                _NOMINATIM_REVERSE_URL,
                params={
                    "lat": lat,
                    "lon": lon,
                    "format": "json",
                    "zoom": 10,
                    "addressdetails": 1,
                    "accept-language": _NOMINATIM_REVERSE_LOCALE,
                },
                headers=_nominatim_headers(_NOMINATIM_REVERSE_LOCALE),
                timeout=5,
            )
            if resp.status_code != 200:
                return ""
            return _format_reverse_location(resp.json())
    except Exception:
        _log.exception("Nominatim reverse search failed")
        return ""


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
        from not_dot_net.backend.vocabularies import field_options
        spec = await field_options(field_cfg.options_key, get_locale())
        select = ui.select(
            label=label, options=spec.options,
            value=value if value in spec.options else None,
            new_value_mode="add-unique" if spec.allow_custom else None,
            with_input=spec.allow_custom,
        ).props("outlined dense stack-label").classes(width_class)
        if spec.allow_custom:
            select.props("use-input fill-input hide-selected")
        fields[field_cfg.name] = select
    elif field_cfg.type == "file":
        uploaded = (files or {}).get(field_cfg.name)
        # Pre-build both views and toggle visibility: clearing the container from
        # inside the Replace click handler would destroy the clicked button.
        with ui.column().classes(f"{width_class} gap-1"):
            upload_view = ui.column().classes("w-full gap-1")
            with upload_view:
                if on_file_upload:
                    req_mark = " *" if field_cfg.required else ""
                    ui.upload(
                        label=f"{label}{req_mark}",
                        auto_upload=True,
                        max_file_size=max_upload_size_mb * 1024 * 1024,
                        on_upload=lambda e, name=field_cfg.name: on_file_upload(name, e),
                    ).props("outlined flat accept='.pdf,.jpg,.jpeg,.png,.doc,.docx'").classes("w-full")
                else:
                    ui.label(f"{label}: {t('no_upload_available')}").classes("text-grey text-sm")

            if uploaded:
                present_view = ui.row().classes("items-center gap-2")
                with present_view:
                    ui.icon("check_circle", color="positive", size="sm")
                    ui.label(f"{label}: {uploaded}").classes("text-positive text-sm")
                    if on_file_upload:
                        def _reveal_upload(present_view=present_view, upload_view=upload_view):
                            present_view.set_visibility(False)
                            upload_view.set_visibility(True)
                        ui.button(t("replace"), on_click=_reveal_upload).props("flat dense size=sm")
                upload_view.set_visibility(False)
        fields[field_cfg.name] = None
    elif field_cfg.type == "location":
        nominatim_results: list[dict] = []
        loc_select = ui.select(
            label=label,
            options={value: value} if value else {},
            value=value or None,
            with_input=True,
        ).props("outlined dense stack-label use-input hide-selected fill-input input-debounce=500").classes("w-full")
        loc_map = ui.leaflet(center=(48.71, 2.21), zoom=4).classes("w-full rounded mt-3").style("height: 450px")
        loc_marker = None
        ignore_next_input = False
        reverse_lookup_running = False

        def _set_marker(lat: float, lon: float):
            nonlocal loc_marker
            loc_map.set_center((lat, lon))
            loc_map.set_zoom(12)
            if loc_marker is not None:
                loc_marker.move(lat, lon)
            else:
                loc_marker = loc_map.marker(latlng=(lat, lon))

        async def _on_input_value(e, sel=loc_select):
            nonlocal ignore_next_input, nominatim_results
            if ignore_next_input:
                ignore_next_input = False
                return
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
                _set_marker(match["lat"], match["lon"])

        async def _on_map_click(e, sel=loc_select):
            nonlocal ignore_next_input, reverse_lookup_running
            if reverse_lookup_running:
                ui.notify(t("location_lookup_wait"), color="warning")
                return
            args = e.args if isinstance(e.args, dict) else {}
            latlng = args.get("latlng") if isinstance(args.get("latlng"), dict) else args
            lat = latlng.get("lat")
            lon = latlng.get("lng", latlng.get("lon"))
            if lat is None or lon is None:
                return
            lat = float(lat)
            lon = float(lon)
            _set_marker(lat, lon)
            reverse_lookup_running = True
            try:
                label = await _nominatim_reverse(lat, lon)
                if not label:
                    label = f"{lat:.5f}, {lon:.5f}"
                ignore_next_input = True
                sel.set_options({label: label})
                sel.set_value(label)
            finally:
                reverse_lookup_running = False

        loc_select.on("input-value", lambda e: asyncio.ensure_future(_on_input_value(e)))
        loc_select.on("update:model-value", _on_select_change)
        loc_map.on("map-click", _on_map_click)
        fields[field_cfg.name] = loc_select
    elif field_cfg.type == "email":
        fields[field_cfg.name] = ui.input(
            label=label, value=value, validation={t("invalid_email"): lambda v: "@" in v if v else True}
        ).props("outlined dense type=email").classes(width_class)
    elif field_cfg.type == "phone":
        from not_dot_net.backend.phone_validation import is_valid_phone
        fields[field_cfg.name] = ui.input(
            label=label, value=value,
            validation={t("invalid_phone"): lambda v: is_valid_phone(v) if v else True},
        ).props("outlined dense type=tel").classes(width_class)
    elif field_cfg.type == "checkbox":
        fields[field_cfg.name] = ui.checkbox(
            text=label, value=bool(value)
        ).classes(width_class)
    else:
        fields[field_cfg.name] = ui.input(
            label=label, value=value
        ).props("outlined dense").classes(width_class)


async def _render_ad_account_creation_form(step, prefill, on_submit):
    """Bespoke form for the AD account creation step.

    Prefills: sAM derived from first/last (cascade), mail from template,
    home dir from template + sam, groups pre-checked by status.
    """
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.workflow_service import (
        derive_sam_candidates,
        render_home,
        render_mail,
    )

    cfg = await ad_account_config.get()

    first = prefill.get("first_name", "")
    last = prefill.get("last_name", "")
    status = prefill.get("status", "")

    state = {
        "sam_account": (derive_sam_candidates(first, last) or [""])[0],
        "ou_dn": "",
        "gid_number": cfg.default_gid_number,
        "login_shell": cfg.default_login_shell,
        "home_directory": "",
        "mail": render_mail(cfg.mail_template, first, last),
        "description": "",
        "notes": "",
        "groups": list(cfg.default_groups_by_status.get(status, [])),
    }
    state["home_directory"] = render_home(cfg.home_directory_template, state["sam_account"])

    with ui.column().classes("w-full gap-3"):
        ui.label(f"{t('first_name')}: {first}").classes("text-sm")
        ui.label(f"{t('last_name')}: {last}").classes("text-sm")

        sam_input = ui.input(t("samaccountname"), value=state["sam_account"]).props("outlined dense stack-label")
        home_input = ui.input(t("home_directory"), value=state["home_directory"]).props("outlined dense stack-label")

        def _on_sam_change(e, _home=home_input):
            state["sam_account"] = (e.value or "").strip()
            state["home_directory"] = render_home(cfg.home_directory_template, state["sam_account"])
            _home.set_value(state["home_directory"])

        sam_input.on_value_change(_on_sam_change)

        ui.label(f"{t('uid')}: {t('uid_allocated_at_submit')}").classes("text-sm text-grey")

        gid_input = ui.number(t("primary_gid"), value=state["gid_number"]).props("outlined dense stack-label")
        gid_input.on_value_change(lambda e: state.update({"gid_number": int(e.value or cfg.default_gid_number)}))

        shell_input = ui.input(t("login_shell"), value=state["login_shell"]).props("outlined dense stack-label")
        shell_input.on_value_change(lambda e: state.update({"login_shell": e.value or cfg.default_login_shell}))

        home_input.on_value_change(lambda e: state.update({"home_directory": e.value or ""}))

        ou_select = ui.select(
            options={dn: dn for dn in cfg.users_ous},
            value=None,
            label=t("ou"),
        ).props("outlined dense stack-label")
        ou_select.on_value_change(lambda e: state.update({"ou_dn": e.value or ""}))

        mail_input = ui.input(t("mail"), value=state["mail"]).props("outlined dense stack-label")
        mail_input.on_value_change(lambda e: state.update({"mail": e.value or ""}))

        desc_input = ui.textarea(t("description"), value=state["description"]).props("outlined dense stack-label")
        desc_input.on_value_change(lambda e: state.update({"description": e.value or ""}))

        groups_select = ui.select(
            options={dn: dn for dn in cfg.eligible_groups},
            value=state["groups"],
            multiple=True,
            label=t("groups"),
        ).props("outlined dense stack-label use-chips")
        groups_select.on_value_change(lambda e: state.update({"groups": list(e.value or [])}))

        notes_input = ui.textarea(t("notes"), value=state["notes"]).props("outlined dense stack-label")
        notes_input.on_value_change(lambda e: state.update({"notes": e.value or ""}))

        async def submit():
            if not state["ou_dn"]:
                ui.notify(t("ou_required"), type="warning")
                return
            payload = {
                **prefill,
                "sam_account": state["sam_account"],
                "ou_dn": state["ou_dn"],
                "gid_number": state["gid_number"],
                "login_shell": state["login_shell"],
                "home_directory": state["home_directory"],
                "mail": state["mail"],
                "description": state["description"],
                "groups": state["groups"],
                "notes": state["notes"],
                "first_name": first,
                "last_name": last,
            }
            await on_submit("complete", payload)

        ui.button(t("complete"), on_click=submit).props("color=primary")


async def resolve_display_values(workflow, data: dict, locale: str) -> dict[str, str]:
    """Map a request's stored values to display strings, resolving select codes
    (which may differ from their label, e.g. nationalities) to their label."""
    defs_cfg = await field_definitions_config.get()
    field_keys: dict[str, str] = {}
    for s in workflow.steps:
        for f in await resolve_step_fields(s, cfg=defs_cfg):
            if f.type == "select" and f.options_key:
                field_keys[f.name] = f.options_key
    resolved: dict[str, str] = {}
    for key, value in data.items():
        options_key = field_keys.get(key)
        if options_key and value:
            terms = {term.code: term for term in await resolve_terms(options_key, active_only=False)}
            term = terms.get(value)
            resolved[key] = term_label(term, locale) if term else value
        else:
            resolved[key] = value
    return resolved


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
    if step.type == "ad_account_creation":
        return await _render_ad_account_creation_form(step, data, on_submit)

    from not_dot_net.config import is_field_visible

    resolved_fields = await resolve_step_fields(step)

    fields: dict = {}
    containers: dict = {}
    state: dict = dict(data)  # mutable working copy for visibility predicates

    # Group consecutive half_width fields into pairs for row layout
    groups: list[list] = []
    for field_cfg in resolved_fields:
        if field_cfg.half_width:
            if groups and isinstance(groups[-1], list) and len(groups[-1]) < 2 and groups[-1][0].half_width:
                groups[-1].append(field_cfg)
            else:
                groups.append([field_cfg])
        else:
            groups.append([field_cfg])

    async def _wrap(field_cfg, width_class):
        with ui.element("div").classes(width_class) as container:
            await _render_field(field_cfg, data, fields, files, on_file_upload, max_upload_size_mb, "w-full")
        containers[field_cfg.name] = container
        container.set_visibility(is_field_visible(field_cfg, state))

    for group in groups:
        is_pair = len(group) == 2 and group[0].half_width
        if is_pair:
            with ui.row().classes("w-full gap-4"):
                for field_cfg in group:
                    await _wrap(field_cfg, "flex-1 min-w-[200px]")
        else:
            await _wrap(group[0], "w-full")

    # After every field is rendered, attach reactivity to checkboxes that
    # are referenced by any visible_when in this step.
    referenced = {
        key
        for f in resolved_fields
        if f.visible_when
        for key in f.visible_when.keys()
    }

    def _refresh_visibility():
        collected = _collect_data(fields)
        state.clear()
        state.update(collected)
        for f in resolved_fields:
            c = containers.get(f.name)
            if c is not None:
                c.set_visibility(is_field_visible(f, state))

    for ref_name in referenced:
        widget = fields.get(ref_name)
        if widget is not None and hasattr(widget, "on_value_change"):
            widget.on_value_change(lambda e, _r=_refresh_visibility: _r())

    # Date-pair: show duration when both departure_date and return_date are set
    if "departure_date" in fields and "return_date" in fields:
        _wire_date_pair(fields["departure_date"], fields["return_date"])

    # Completion status for partial-save steps
    if step.partial_save:
        _render_completion_indicator(resolved_fields, data, files or {})

    with ui.row().classes("mt-4 gap-2"):
        if on_save_draft and step.partial_save:
            ui.button(t("save_draft"), on_click=lambda: on_save_draft(_collect_data(fields))).props(
                "flat"
            )

        async def validated_submit():
            collected = _collect_data(fields)
            missing = [
                t(f.label) if f.label else f.name for f in resolved_fields
                if f.required
                and is_field_visible(f, collected)
                and not _field_is_filled(f, collected, files or {})
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


def _field_is_filled(field, data: dict, files: dict) -> bool:
    """A file field is filled when an upload was recorded; others when data is set."""
    if field.type == "file":
        return bool(files.get(field.name))
    return bool(data.get(field.name))


def _render_completion_indicator(fields: list, data: dict, files: dict):
    """Show which required, currently-visible fields are filled (partial save)."""
    from not_dot_net.config import is_field_visible
    required = [f for f in fields if f.required and is_field_visible(f, data)]
    if not required:
        return
    filled = sum(1 for f in required if _field_is_filled(f, data, files))
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
                label = f"✓ {step_display(step)}"
                cls = "text-[11px] text-positive font-medium flex-1"
            elif i == current_idx:
                label = f"● {step_display(step)}"
                cls = "text-[11px] text-primary font-semibold flex-1"
            else:
                label = step_display(step)
                cls = "text-[11px] text-grey-7 flex-1"
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
