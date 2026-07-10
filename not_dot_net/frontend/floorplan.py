"""Floor Plan tab — view and (admin) manage building floor plans and pins."""

import base64
from xml.sax.saxutils import escape

from nicegui import ui

from not_dot_net.backend.db import User
from not_dot_net.backend.floorplan_models import MapPoint
from not_dot_net.backend.floorplan_service import (
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
                ui.button(t("floorplan_add"), icon="add").props("color=primary")
            return

        state = {"selected": plans[0], "highlight_id": None}
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

        image = ui.interactive_image(
            source=_floorplan_image_data_uri(image_bytes),
            content=_points_svg(points, state["highlight_id"]),
        ).classes("w-full border rounded")

        async def on_mouse(e):
            hit = nearest_map_point(points, round(e.image_x), round(e.image_y))
            state["highlight_id"] = hit.id if hit else None
            image.content = _points_svg(points, state["highlight_id"])
            if hit:
                ui.notify(hit.label)

        image.on_mouse(on_mouse)
