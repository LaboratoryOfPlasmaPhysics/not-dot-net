"""Floor Plan tab — view and (admin) manage building floor plans and pins."""

import base64
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
                if is_admin:
                    await _show_pin_actions(plan_area, state, user, is_admin, hit)
                else:
                    ui.notify(hit.label)

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
    with ui.dialog() as dialog, ui.card().classes("w-72"):
        ui.label(point.label).classes("text-h6")
        ui.label(t(f"kind_{point.kind}")).classes("text-sm text-grey")

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            async def do_delete():
                dialog.close()
                await delete_map_point(point.id, actor=user)
                ui.notify(t("floorplan_pin_deleted"), color="positive")
                await _render_plan_area(plan_area, state, user, is_admin)

            ui.button(t("delete"), icon="delete", on_click=do_delete).props("color=negative")
    dialog.open()
