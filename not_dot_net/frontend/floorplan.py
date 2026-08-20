"""Floor Plan tab — view and (admin) manage building floor plans and pins."""

import base64
import uuid
from datetime import date, timedelta
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from nicegui import ui

from not_dot_net.backend.booking_service import get_resource_by_id, list_resources
from not_dot_net.backend.db import User, resolve_user_names
from not_dot_net.backend.floorplan_models import MapPoint
from not_dot_net.backend.floorplan_service import (
    add_map_point,
    create_floor_plan,
    delete_floor_plan,
    delete_map_point,
    floor_plan_image_exists,
    get_floor_plan_image,
    list_floor_plans,
    list_map_points,
    update_map_point_geometry,
)
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.frontend.floorplan_leaflet import FloorPlanLeaflet
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.errors import notify_error
from not_dot_net.frontend.widgets import confirm_dialog

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
# Plans are re-encoded to FLOORPLAN_MAX_DIMENSION_PX on the way in; this only
# bounds what an admin can push through memory before that happens.
FLOORPLAN_MAX_UPLOAD_MB = 25

PIN_KINDS = ["room", "desk", "wall_plug", "asset", "other"]


def _pin_kind_select_options() -> dict[str, str]:
    return {kind: t(f"kind_{kind}") for kind in PIN_KINDS}


def _resource_picker_visible(kind: str) -> bool:
    """Only "room" pins can be linked to an office resource — linking one to
    another kind would create a dead link, since the office-availability UI
    only ever renders for room-kind pins (see is_office in _show_pin_actions)."""
    return kind == "room"


def _should_place_pin(is_admin: bool, mode: str) -> bool:
    return is_admin and mode == "place"


def _should_draw_zone(is_admin: bool, mode: str) -> bool:
    return is_admin and mode == "draw"


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



floorplan_router = APIRouter(tags=["floorplan"])


@floorplan_router.get("/floorplan/image/{floor_plan_id}")
async def serve_floorplan_image(floor_plan_id: str):
    """Serve a plan image. Content is immutable per id — plans have no
    image-replacement path, only create and delete — so it caches hard.

    Unauthenticated, matching the floor plan page itself: these same bytes were
    already shipped to guests inside the page.
    """
    try:
        plan_id = uuid.UUID(floor_plan_id)
    except ValueError:
        raise HTTPException(status_code=404)

    content = await get_floor_plan_image(plan_id)
    if content is None:
        raise HTTPException(status_code=404)
    return Response(
        content=content,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def _floorplan_image_data_uri(content: bytes) -> str:
    b64 = base64.b64encode(content).decode()
    return f"data:image/jpeg;base64,{b64}"


def _floorplan_image_url(floor_plan_id) -> str:
    """Cacheable URL for a plan image, instead of a multi-MB base64 prop.

    The plan area re-renders on every pin add/delete, zone edit, availability
    change and office booking; embedding the image meant re-reading it from
    disk and pushing it over the websocket each time.
    """
    return f"/floorplan/image/{floor_plan_id}"


def _points_payload(points: list[MapPoint], highlight_id=None) -> list[dict]:
    """Marker data for FloorPlanLeaflet. Labels are HTML-escaped because
    Leaflet's bindTooltip sets tooltip content via innerHTML for string
    content — an unescaped admin-entered label would inject markup into
    every viewer's page."""
    return [
        {
            "id": str(point.id),
            "x": point.x,
            "y": point.y,
            "label": escape(point.label),
            "color": _KIND_COLOR.get(point.kind, "#757575"),
            "highlighted": point.id == highlight_id,
            "kind": point.kind,
            "polygon": point.polygon,
        }
        for point in points
    ]


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

        state = {"selected": plans[0], "highlight_id": None, "place_mode": "off", "editing_point_id": None}

        # Declare the switcher/admin row *before* creating plan_area: NiceGUI
        # assigns DOM position at element-creation time, not at content-fill
        # time, so plan_area must not be created first or these end up
        # rendered below the map regardless of statement order.
        tabs = None
        if len(plans) > 1:
            with ui.tabs().classes("w-full mb-2") as tabs:
                for p in plans:
                    ui.tab(name=str(p.id), label=p.name)
            tabs.value = str(state["selected"].id)

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

        plan_area = ui.column().classes("w-full")

        if tabs is not None:
            async def on_select(e):
                state["selected"] = next(p for p in plans if str(p.id) == e.value)
                state["highlight_id"] = None
                await _render_plan_area(plan_area, state, user, is_admin)

            tabs.on_value_change(on_select)

        await _render_plan_area(plan_area, state, user, is_admin)


async def _render_plan_area(plan_area, state, user, is_admin):
    plan_area.clear()
    plan = state["selected"]
    has_image = await floor_plan_image_exists(plan.id)
    points = await list_map_points(plan.id)
    points_by_id = {str(p.id): p for p in points}
    editing_id = state.get("editing_point_id")

    with plan_area:
        if not has_image:
            ui.label(t("floorplan_none")).classes("text-grey")
            return

        # Container created before `leaflet` so its controls render above the
        # map (DOM position = element-creation time, not content-fill time —
        # see the switcher-position gotcha this file already learned once).
        # Its content is filled in below, after `leaflet` exists, so the
        # closures here can reference it without any forward-declaration.
        controls_row = ui.column().classes("w-full")

        leaflet_mode = "editing" if editing_id is not None else state.get("place_mode", "off")
        visible_kinds = state.setdefault("visible_kinds", list(PIN_KINDS))
        leaflet = FloorPlanLeaflet(
            image_url=_floorplan_image_url(plan.id),
            width_px=plan.width_px, height_px=plan.height_px,
            points=_points_payload(points, state["highlight_id"]),
            mode=leaflet_mode,
            visible_kinds=visible_kinds,
        )
        if editing_id is not None:
            leaflet.set_editing_point(editing_id)

        with controls_row:
            if editing_id is not None:
                with ui.row().classes("items-center gap-2"):
                    ui.label(t("floorplan_edit_shape")).classes("text-sm font-bold")

                    async def do_cancel_edit():
                        state["editing_point_id"] = None
                        await _render_plan_area(plan_area, state, user, is_admin)

                    async def do_finish_edit():
                        vertices = await leaflet.finish_editing()
                        target = points_by_id.get(editing_id)
                        state["editing_point_id"] = None
                        if vertices and target is not None:
                            await update_map_point_geometry(target.id, vertices, actor=user)
                            ui.notify(t("floorplan_shape_updated"), color="positive")
                        await _render_plan_area(plan_area, state, user, is_admin)

                    ui.button(t("cancel"), on_click=do_cancel_edit).props("flat dense")
                    ui.button(t("floorplan_finish_edit"), on_click=do_finish_edit).props("dense color=primary")
            elif is_admin:
                mode_options = {
                    "off": t("floorplan_mode_off"),
                    "place": t("floorplan_place_pin_mode"),
                    "draw": t("floorplan_draw_zone_mode"),
                }

                def on_mode_change(e):
                    state["place_mode"] = e.value
                    leaflet.set_mode(e.value)

                ui.toggle(mode_options, value=state.get("place_mode", "off"), on_change=on_mode_change)

            with ui.row().classes("items-center gap-2 mt-1"):
                kind_labels = _pin_kind_select_options()
                for kind in PIN_KINDS:
                    def on_kind_toggle(e, kind=kind):
                        kinds = state["visible_kinds"]
                        if e.value and kind not in kinds:
                            kinds.append(kind)
                        elif not e.value and kind in kinds:
                            kinds.remove(kind)
                        leaflet.set_visible_kinds(list(kinds))

                    ui.checkbox(kind_labels[kind], value=kind in visible_kinds, on_change=on_kind_toggle)

        async def on_image_click(e):
            if not _should_place_pin(is_admin, state.get("place_mode", "off")):
                return
            x, y = round(e.args["x"]), round(e.args["y"])
            await _show_add_pin_dialog(plan_area, state, user, is_admin, plan.id, x, y)

        async def on_zone_drawn(e):
            if not _should_draw_zone(is_admin, state.get("place_mode", "off")):
                return
            vertices = [[round(v[0]), round(v[1])] for v in e.args["vertices"]]
            await _show_add_pin_dialog(
                plan_area, state, user, is_admin, plan.id, vertices[0][0], vertices[0][1],
                polygon=vertices,
            )

        async def on_pin_click(e):
            hit = points_by_id.get(e.args["id"])
            if hit is None:
                return
            state["highlight_id"] = hit.id
            leaflet.set_points(_points_payload(points, state["highlight_id"]))
            await _show_pin_actions(plan_area, state, user, is_admin, hit, leaflet=leaflet)

        leaflet.on("image-click", on_image_click)
        leaflet.on("zone-drawn", on_zone_drawn)
        leaflet.on("pin-click", on_pin_click)


async def _show_add_plan_dialog(container, user):
    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label(t("floorplan_add")).classes("text-h6")
        name_input = ui.input(t("floorplan_name")).props("outlined dense").classes("w-full")
        state = {"content": None}

        max_bytes = FLOORPLAN_MAX_UPLOAD_MB * 1024 * 1024

        async def handle_upload(e):
            content = await e.file.read()
            if len(content) > max_bytes:
                state["content"] = None
                ui.notify(
                    t("floorplan_too_large", max_size_mb=FLOORPLAN_MAX_UPLOAD_MB),
                    color="negative",
                )
                return
            state["content"] = content

        ui.upload(
            label=t("floorplan_upload_image"), on_upload=handle_upload, auto_upload=True,
            max_file_size=max_bytes,
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
                    if isinstance(exc, ValueError):
                        ui.notify(t("floorplan_upload_failed"), color="negative")
                    else:
                        notify_error(exc)
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
                notify_error(exc)
                return
            ui.notify(t("floorplan_deleted"), color="positive")
            await _render_floorplan(container, user)

        with ui.row():
            ui.button(t("cancel"), on_click=dialog.close).props("flat")
            ui.button(t("delete"), on_click=confirm).props("color=negative")
    dialog.open()


async def _show_add_pin_dialog(plan_area, state, user, is_admin, floor_plan_id, x, y, polygon=None):
    offices = [r for r in await list_resources(active_only=True) if r.resource_type == "office"]
    resource_options = {None: t("floorplan_no_resource"), **{r.id: r.name for r in offices}}

    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label(t("floorplan_pin_label")).classes("text-subtitle2")
        label_input = ui.input(t("floorplan_pin_label")).props("outlined dense").classes("w-full")
        kind_select = ui.select(
            _pin_kind_select_options(), value="room", label=t("floorplan_pin_kind"),
        ).props("outlined dense").classes("w-full")
        resource_container = ui.column().classes("w-full")
        with resource_container:
            resource_select = ui.select(
                resource_options, value=None, label=t("floorplan_link_resource"),
            ).props("outlined dense with-input").classes("w-full")
        resource_container.set_visibility(_resource_picker_visible(kind_select.value))

        def on_kind_change(e):
            visible = _resource_picker_visible(e.value)
            resource_container.set_visibility(visible)
            if not visible:
                resource_select.value = None

        kind_select.on_value_change(on_kind_change)

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            async def do_save():
                if not label_input.value.strip():
                    ui.notify(t("required_field"), color="negative")
                    return
                await add_map_point(
                    floor_plan_id, label_input.value.strip(), kind_select.value, x, y,
                    resource_id=resource_select.value, polygon=polygon, actor=user,
                )
                ui.notify(t("floorplan_pin_added"), color="positive")
                dialog.close()
                await _render_plan_area(plan_area, state, user, is_admin)

            ui.button(t("save"), on_click=do_save).props("color=primary")
    dialog.open()


async def _show_pin_actions(plan_area, state, user, is_admin, point, leaflet=None):
    resource = None
    if point.resource_id is not None:
        resource = await get_resource_by_id(point.resource_id)
    is_office = point.kind == "room" and resource is not None and resource.resource_type == "office"
    can_edit_resource = is_office and await has_permissions(user, "manage_bookings")
    can_edit_shape = is_admin and point.polygon is not None and leaflet is not None

    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label(point.label).classes("text-h6")
        ui.label(t(f"kind_{point.kind}")).classes("text-sm text-grey")

        if is_office:
            owner_names = await resolve_user_names([resource.owner_user_id])
            owner_label = owner_names.get(resource.owner_user_id, t("no_owner"))
            ui.label(f"{t('resource_owner')}: {owner_label}").classes("text-sm")
            await _render_office_availability_section(dialog, plan_area, state, user, is_admin, resource)

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            if can_edit_resource:
                async def do_edit():
                    from not_dot_net.frontend.bookings import _show_resource_dialog

                    dialog.close()
                    await _show_resource_dialog(
                        None, user, resource=resource,
                        on_saved=lambda: _render_plan_area(plan_area, state, user, is_admin),
                    )

                ui.button(t("edit_resource"), icon="edit", on_click=do_edit).props(
                    "flat dense color=primary"
                )

            if can_edit_shape:
                async def do_edit_shape():
                    dialog.close()
                    state["editing_point_id"] = str(point.id)
                    await _render_plan_area(plan_area, state, user, is_admin)

                ui.button(t("floorplan_edit_shape"), icon="edit_location", on_click=do_edit_shape).props(
                    "flat dense color=primary"
                )

            if is_admin:
                async def do_delete():
                    dialog.close()
                    await delete_map_point(point.id, actor=user)
                    ui.notify(t("floorplan_pin_deleted"), color="positive")
                    await _render_plan_area(plan_area, state, user, is_admin)

                delete_dlg = confirm_dialog(
                    t("confirm_delete_pin", label=point.label or ""), do_delete,
                    confirm_label=t("delete"),
                )
                ui.button(t("delete"), icon="delete", on_click=delete_dlg.open).props(
                    "color=negative"
                )
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
                            notify_error(exc)
                            return
                        ui.notify(t("floorplan_availability_revoked"), color="positive")
                        dialog.close()
                        await _render_plan_area(plan_area, state, user, is_admin)

                    revoke_dlg = confirm_dialog(
                        t("confirm_revoke_availability"), do_revoke,
                        confirm_label=t("floorplan_revoke"), confirm_icon="event_busy",
                    )
                    ui.button(icon="close", on_click=revoke_dlg.open).props(
                        "flat dense round size=xs color=negative"
                    ).tooltip(t("floorplan_revoke"))
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
                    notify_error(exc)
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
                        notify_error(exc)
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
