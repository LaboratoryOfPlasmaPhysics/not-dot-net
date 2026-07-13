from nicegui import ui
from nicegui.testing import User


async def test_floorplan_leaflet_default_props(user: User) -> None:
    from not_dot_net.frontend.floorplan_leaflet import ALL_KINDS, FloorPlanLeaflet

    holder = {}

    @ui.page("/floorplan-leaflet-defaults-test")
    def page():
        holder["element"] = FloorPlanLeaflet(image_url="data:image/jpeg;base64,x", width_px=100, height_px=80)

    await user.open("/floorplan-leaflet-defaults-test")
    element = holder["element"]
    assert element._props["mode"] == "off"
    assert element._props["visibleKinds"] == ALL_KINDS
    assert element._props["editingPointId"] is None


async def test_floorplan_leaflet_constructor_accepts_mode_and_visible_kinds(user: User) -> None:
    from not_dot_net.frontend.floorplan_leaflet import FloorPlanLeaflet

    holder = {}

    @ui.page("/floorplan-leaflet-ctor-test")
    def page():
        holder["element"] = FloorPlanLeaflet(
            image_url="x", width_px=10, height_px=10, mode="draw", visible_kinds=["room"],
        )

    await user.open("/floorplan-leaflet-ctor-test")
    element = holder["element"]
    assert element._props["mode"] == "draw"
    assert element._props["visibleKinds"] == ["room"]


async def test_floorplan_leaflet_set_mode_updates_prop(user: User) -> None:
    from not_dot_net.frontend.floorplan_leaflet import FloorPlanLeaflet

    holder = {}

    @ui.page("/floorplan-leaflet-set-mode-test")
    def page():
        holder["element"] = FloorPlanLeaflet(image_url="x", width_px=10, height_px=10)

    await user.open("/floorplan-leaflet-set-mode-test")
    element = holder["element"]
    element.set_mode("editing")
    assert element._props["mode"] == "editing"


async def test_floorplan_leaflet_set_visible_kinds_updates_prop(user: User) -> None:
    from not_dot_net.frontend.floorplan_leaflet import FloorPlanLeaflet

    holder = {}

    @ui.page("/floorplan-leaflet-set-kinds-test")
    def page():
        holder["element"] = FloorPlanLeaflet(image_url="x", width_px=10, height_px=10)

    await user.open("/floorplan-leaflet-set-kinds-test")
    element = holder["element"]
    element.set_visible_kinds(["room", "desk"])
    assert element._props["visibleKinds"] == ["room", "desk"]


async def test_floorplan_leaflet_set_editing_point_updates_prop(user: User) -> None:
    from not_dot_net.frontend.floorplan_leaflet import FloorPlanLeaflet

    holder = {}

    @ui.page("/floorplan-leaflet-set-editing-test")
    def page():
        holder["element"] = FloorPlanLeaflet(image_url="x", width_px=10, height_px=10)

    await user.open("/floorplan-leaflet-set-editing-test")
    element = holder["element"]
    element.set_editing_point("abc-123")
    assert element._props["editingPointId"] == "abc-123"
