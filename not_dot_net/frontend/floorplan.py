"""Floor Plan tab — view and (admin) manage building floor plans and pins."""

import base64
from datetime import date, timedelta
from xml.sax.saxutils import escape

from nicegui import ui

from not_dot_net.backend.booking_service import get_resource_by_id, list_resources
from not_dot_net.backend.db import User
from not_dot_net.backend.floorplan_models import MapPoint
from not_dot_net.backend.floorplan_service import (
    add_map_point,
    create_floor_plan,
    delete_floor_plan,
    delete_map_point,
    get_floor_plan_image,
    list_floor_plans,
    list_map_points,
    nearest_map_point,
)
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.frontend.i18n import t

_KIND_COLOR = {
    "room": "#1976d2",
    "desk": "#43a047",
    "wall_plug": "#e53935",
    "asset": "#8e24aa",
    "other": "#757575",
}

# Plain keys only — do NOT resolve translations at module import time. `t()`
# reads `app.storage.user` via `get_locale()`, which requires an active
# NiceGUI page/client context; calling it at import time raises. Build the
# translated {key: label} dict inside a render function instead (see
# `_pin_kind_select_options` below).
PIN_KINDS = ["room", "desk", "wall_plug", "asset", "other"]


def _pin_kind_select_options() -> dict[str, str]:
    return {kind: t(f"kind_{kind}") for kind in PIN_KINDS}


def _qdate_option_date(value) -> str:
    return value.isoformat().replace("-", "/")


def _earliest_office_book_start(window_start, today, minimum_lead_days: int) -> date:
    """The earliest bookable start date for an offered office window: the
    window's own start, floored by the org-wide `minimum_lead_days` policy
    that `create_booking` enforces for every resource, offices included."""
    return max(window_start, today + timedelta(days=minimum_lead_days))


def _clamp_range_to_window(value, window_start, window_end_exclusive) -> dict[str, str]:
    """Clamp a date-range dict to [window_start, window_end_exclusive). Falls
    back to the full window when value is missing/invalid."""
    window_last_day = window_end_exclusive - timedelta(days=1)
    default = {"from": str(window_start), "to": str(window_last_day)}
    if not isinstance(value, dict):
        return default
    try:
        start = date.fromisoformat(value["from"])
        end = date.fromisoformat(value["to"])
    except (KeyError, TypeError, ValueError):
        return default
    start = max(start, window_start)
    end = min(end, window_last_day)
    if start > end:
        return default
    return {"from": str(start), "to": str(end)}


def _floorplan_image_data_uri(content: bytes) -> str:
    b64 = base64.b64encode(content).decode()
    return f"data:image/jpeg;base64,{b64}"


def _points_svg(points: list[MapPoint], highlight_id=None) -> str:
    parts = []
    for point in points:
        color = _KIND_COLOR.get(point.kind, "#757575")
        stroke = ' stroke="black" stroke-width="2"' if point.id == highlight_id else ""
        parts.append(
            f'<circle cx="{point.x}" cy="{point.y}" r="8" fill="{color}"{stroke}/>'
            f'<text x="{point.x + 10}" y="{point.y + 4}" font-size="12" '
            f'fill="black" stroke="white" stroke-width="3" paint-order="stroke">'
            f'{escape(point.label)}</text>'
        )
    return "".join(parts)


def render(user: User):
    container = ui.column().classes("w-full")

    async def refresh():
        await _render_floorplan(container, user)

    ui.timer(0, refresh, once=True)
    return refresh


async def _render_floorplan(container, user: User):
    container.clear()
    is_admin = await has_permissions(user, "manage_floorplans")
    plans = await list_floor_plans()

    with container:
        if not plans:
            ui.label(t("floorplan_none")).classes("text-grey")
            if is_admin:
                ui.button(
                    t("floorplan_add"), icon="add",
                    on_click=lambda: _show_add_plan_dialog(container, user),
                ).props("color=primary")
            return

        state = {"selected": plans[0], "highlight_id": None, "place_mode": False}
        plan_area = ui.column().classes("w-full")

        if len(plans) > 1:
            select = ui.select(
                {p.id: p.name for p in plans}, value=state["selected"].id,
                label=t("floorplan_select"),
            ).props("outlined dense").classes("w-64 mb-2")

            async def on_select(e):
                state["selected"] = next(p for p in plans if p.id == e.value)
                state["highlight_id"] = None
                await _render_plan_area(plan_area, state, user, is_admin)

            select.on_value_change(on_select)

        if is_admin:
            with ui.row().classes("gap-2 mb-2"):
                ui.button(
                    t("floorplan_add"), icon="add",
                    on_click=lambda: _show_add_plan_dialog(container, user),
                ).props("flat dense color=primary")
                ui.button(
                    t("delete"), icon="delete",
                    on_click=lambda: _confirm_delete_plan(container, user, state["selected"]),
                ).props("flat dense color=negative")

        await _render_plan_area(plan_area, state, user, is_admin)


async def _render_plan_area(plan_area, state, user, is_admin):
    plan_area.clear()
    plan = state["selected"]
    image_bytes = await get_floor_plan_image(plan.id)
    points = await list_map_points(plan.id)

    with plan_area:
        if image_bytes is None:
            ui.label(t("floorplan_none")).classes("text-grey")
            return

        if is_admin:
            # Preserve place-pin mode across re-renders (e.g. right after adding
            # a pin) — otherwise the switch resets to off and an admin placing
            # several pins in a row has to re-toggle it before every click.
            ui.switch(t("floorplan_place_pin_mode"), value=state.get("place_mode", False),
                      on_change=lambda e: state.__setitem__("place_mode", e.value))

        image = ui.interactive_image(
            source=_floorplan_image_data_uri(image_bytes),
            content=_points_svg(points, state["highlight_id"]),
        ).classes("w-full border rounded")

        async def on_mouse(e):
            x, y = round(e.image_x), round(e.image_y)
            if is_admin and state.get("place_mode", False):
                await _show_add_pin_dialog(plan_area, state, user, is_admin, plan.id, x, y)
                return
            hit = nearest_map_point(points, x, y)
            state["highlight_id"] = hit.id if hit else None
            image.content = _points_svg(points, state["highlight_id"])
            if hit:
                await _show_pin_actions(plan_area, state, user, is_admin, hit)

        image.on_mouse(on_mouse)


async def _show_add_plan_dialog(container, user):
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label(t("floorplan_add")).classes("text-h6")
        name_input = ui.input(t("floorplan_name")).props("outlined dense").classes("w-full")
        state = {"content": None}

        async def handle_upload(e):
            state["content"] = await e.file.read()

        ui.upload(
            label=t("floorplan_upload_image"), on_upload=handle_upload, auto_upload=True,
        ).props("accept=.jpg,.jpeg,.png").classes("w-full")

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            async def do_save():
                if not name_input.value.strip() or state["content"] is None:
                    ui.notify(t("required_field"), color="negative")
                    return
                try:
                    await create_floor_plan(name_input.value.strip(), state["content"], actor=user)
                except (ValueError, PermissionError) as exc:
                    ui.notify(t("floorplan_upload_failed") if isinstance(exc, ValueError) else str(exc),
                              color="negative")
                    return
                ui.notify(t("floorplan_uploaded"), color="positive")
                dialog.close()
                await _render_floorplan(container, user)

            ui.button(t("save"), on_click=do_save).props("color=primary")
    dialog.open()


async def _confirm_delete_plan(container, user, plan):
    with ui.dialog() as dialog, ui.card():
        ui.label(t("floorplan_delete_confirm"))

        async def confirm():
            dialog.close()
            try:
                await delete_floor_plan(plan.id, actor=user)
            except PermissionError as exc:
                ui.notify(str(exc), color="negative")
                return
            ui.notify(t("floorplan_deleted"), color="positive")
            await _render_floorplan(container, user)

        with ui.row():
            ui.button(t("cancel"), on_click=dialog.close).props("flat")
            ui.button(t("delete"), on_click=confirm).props("color=negative")
    dialog.open()


async def _show_add_pin_dialog(plan_area, state, user, is_admin, floor_plan_id, x, y):
    offices = [r for r in await list_resources(active_only=True) if r.resource_type == "office"]
    resource_options = {None: t("floorplan_no_resource"), **{r.id: r.name for r in offices}}

    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label(t("floorplan_pin_label")).classes("text-subtitle2")
        label_input = ui.input(t("floorplan_pin_label")).props("outlined dense").classes("w-full")
        kind_select = ui.select(
            _pin_kind_select_options(), value="room", label=t("floorplan_pin_kind"),
        ).props("outlined dense").classes("w-full")
        resource_select = ui.select(
            resource_options, value=None, label=t("floorplan_link_resource"),
        ).props("outlined dense with-input").classes("w-full")

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            async def do_save():
                if not label_input.value.strip():
                    ui.notify(t("required_field"), color="negative")
                    return
                await add_map_point(
                    floor_plan_id, label_input.value.strip(), kind_select.value, x, y,
                    resource_id=resource_select.value, actor=user,
                )
                ui.notify(t("floorplan_pin_added"), color="positive")
                dialog.close()
                await _render_plan_area(plan_area, state, user, is_admin)

            ui.button(t("save"), on_click=do_save).props("color=primary")
    dialog.open()


async def _show_pin_actions(plan_area, state, user, is_admin, point):
    resource = None
    if point.resource_id is not None:
        resource = await get_resource_by_id(point.resource_id)
    is_office = point.kind == "room" and resource is not None and resource.resource_type == "office"

    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label(point.label).classes("text-h6")
        ui.label(t(f"kind_{point.kind}")).classes("text-sm text-grey")

        if is_office:
            await _render_office_availability_section(dialog, plan_area, state, user, is_admin, resource)

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            if is_admin:
                async def do_delete():
                    dialog.close()
                    await delete_map_point(point.id, actor=user)
                    ui.notify(t("floorplan_pin_deleted"), color="positive")
                    await _render_plan_area(plan_area, state, user, is_admin)

                ui.button(t("delete"), icon="delete", on_click=do_delete).props("color=negative")
    dialog.open()


async def _render_office_availability_section(dialog, plan_area, state, user, is_admin, resource):
    from not_dot_net.backend.office_availability import (
        OfficeAvailabilityError,
        list_availability_windows,
        revoke_availability,
    )
    from not_dot_net.frontend.bookings import _format_booking_period

    is_owner = user.is_active and user.id == resource.owner_user_id
    can_manage_bookings = await has_permissions(user, "manage_bookings")
    can_offer = is_owner or can_manage_bookings
    windows = await list_availability_windows(resource.id)
    today = date.today()
    open_windows = [w for w in windows if w.end_date > today]

    ui.separator().classes("my-2")
    if open_windows:
        ui.label(t("floorplan_availability_open")).classes("text-sm font-bold")
        for w in open_windows:
            with ui.row().classes("items-center gap-2"):
                ui.label(_format_booking_period(w.start_date, w.end_date)).classes("text-sm text-grey-8")
                if can_offer:
                    async def do_revoke(window=w):
                        try:
                            await revoke_availability(window.id, actor=user)
                        except (PermissionError, OfficeAvailabilityError) as exc:
                            ui.notify(str(exc), color="negative")
                            return
                        ui.notify(t("floorplan_availability_revoked"), color="positive")
                        dialog.close()
                        await _render_plan_area(plan_area, state, user, is_admin)

                    ui.button(icon="close", on_click=do_revoke).props(
                        "flat dense round size=xs color=negative"
                    )
    else:
        ui.label(t("floorplan_availability_none")).classes("text-sm text-grey")

    with ui.row().classes("gap-2 mt-2"):
        if can_offer:
            ui.button(
                t("floorplan_offer_availability"),
                on_click=lambda: _show_offer_dialog(dialog, plan_area, state, user, is_admin, resource),
            ).props("flat dense color=primary")
        if open_windows and user.is_active:
            ui.button(
                t("book"),
                on_click=lambda: _show_office_book_dialog(
                    dialog, plan_area, state, user, is_admin, resource, open_windows,
                ),
            ).props("flat dense color=primary")


async def _show_offer_dialog(parent_dialog, plan_area, state, user, is_admin, resource):
    from not_dot_net.backend.office_availability import OfficeAvailabilityError, offer_availability

    parent_dialog.close()
    today = date.today()
    default_range = {"from": str(today), "to": str(today + timedelta(days=14))}

    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label(t("floorplan_offer_availability")).classes("text-h6")
        min_option = _qdate_option_date(today)
        date_picker = ui.date(default_range).props(
            f"range :options=\"date => date >= '{min_option}'\""
        )

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            async def do_save():
                val = date_picker.value
                if not isinstance(val, dict):
                    ui.notify(t("required_field"), color="negative")
                    return
                start = date.fromisoformat(val["from"])
                end = date.fromisoformat(val["to"]) + timedelta(days=1)
                try:
                    await offer_availability(resource.id, start, end, actor=user)
                except (PermissionError, OfficeAvailabilityError) as exc:
                    ui.notify(str(exc), color="negative")
                    return
                ui.notify(t("floorplan_availability_offered"), color="positive")
                dialog.close()
                await _render_plan_area(plan_area, state, user, is_admin)

            ui.button(t("save"), on_click=do_save).props("color=primary")
    dialog.open()


async def _show_office_book_dialog(parent_dialog, plan_area, state, user, is_admin, resource, open_windows):
    from not_dot_net.backend.booking_service import BookingConflictError, BookingValidationError, create_booking
    from not_dot_net.config import bookings_config
    from not_dot_net.frontend.bookings import _format_booking_period

    parent_dialog.close()

    async def _open_for_window(window):
        cfg = await bookings_config.get()
        earliest_start = _earliest_office_book_start(
            window.start_date, date.today(), cfg.minimum_lead_days,
        )
        with ui.dialog() as dialog, ui.card().classes("w-80"):
            ui.label(t("book")).classes("text-h6")
            default_range = _clamp_range_to_window(None, earliest_start, window.end_date)
            min_option = _qdate_option_date(earliest_start)
            max_option = _qdate_option_date(window.end_date - timedelta(days=1))
            date_picker = ui.date(default_range).props(
                f"range :options=\"date => date >= '{min_option}' && date <= '{max_option}'\""
            )
            note_input = ui.input(t("note")).props("outlined dense").classes("w-full")

            with ui.row().classes("justify-end gap-2 mt-2"):
                ui.button(t("cancel"), on_click=dialog.close).props("flat")

                async def do_book():
                    val = _clamp_range_to_window(date_picker.value, earliest_start, window.end_date)
                    start = date.fromisoformat(val["from"])
                    end = date.fromisoformat(val["to"]) + timedelta(days=1)
                    try:
                        await create_booking(
                            resource.id, user.id, start, end,
                            note=note_input.value, actor=user,
                        )
                    except (BookingConflictError, BookingValidationError) as exc:
                        ui.notify(str(exc), color="negative")
                        return
                    ui.notify(t("booking_created"), color="positive")
                    dialog.close()
                    await _render_plan_area(plan_area, state, user, is_admin)

                ui.button(t("book"), on_click=do_book).props("color=primary")
        dialog.open()

    if len(open_windows) == 1:
        await _open_for_window(open_windows[0])
        return

    with ui.dialog() as select_dialog, ui.card().classes("w-80"):
        ui.label(t("floorplan_choose_window")).classes("text-h6")
        options = {
            i: _format_booking_period(w.start_date, w.end_date) for i, w in enumerate(open_windows)
        }
        window_select = ui.select(
            options, label=t("floorplan_choose_window"),
        ).props("outlined dense").classes("w-full")

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=select_dialog.close).props("flat")

            async def do_continue():
                if window_select.value is None:
                    ui.notify(t("required_field"), color="negative")
                    return
                chosen = open_windows[window_select.value]
                select_dialog.close()
                await _open_for_window(chosen)

            ui.button(t("continue"), on_click=do_continue).props("color=primary")
    select_dialog.open()
