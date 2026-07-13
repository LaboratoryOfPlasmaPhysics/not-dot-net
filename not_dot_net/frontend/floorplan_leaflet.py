"""Pannable/zoomable floor-plan image, backed by Leaflet's CRS.Simple mode.

NiceGUI's own `ui.leaflet` cannot be configured for CRS.Simple: its `options`
dict is JSON-serialized to the client, so `L.CRS.Simple` (a JS object with
project/unproject/scale methods) collapses to the literal string
"L.CRS.Simple" — which crashes Leaflet's `setView` at construction time. This
element hardcodes `crs: L.CRS.Simple` in its own JS instead, reusing NiceGUI's
already-bundled Leaflet assets (no extra download, no CDN). It also drives
Leaflet.draw (also bundled with NiceGUI) for drawing/reshaping polygon zones.
"""

from pathlib import Path

import nicegui.elements.leaflet as _ng_leaflet
from nicegui.awaitable_response import AwaitableResponse
from nicegui.element import Element

_LEAFLET_DIST = Path(_ng_leaflet.__file__).parent / "dist"
_HERE = Path(__file__).parent

ALL_KINDS = ["room", "desk", "wall_plug", "asset", "other"]


class FloorPlanLeaflet(
    Element,
    component=_HERE / "floorplan_leaflet.js",
    esm={"nicegui-leaflet": _LEAFLET_DIST},
    default_classes="w-full border rounded",
):
    def __init__(
        self, *, image_url: str, width_px: int, height_px: int, points: list[dict] | None = None,
        mode: str = "off", visible_kinds: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.add_resource(_LEAFLET_DIST)
        self._props["imageUrl"] = image_url
        self._props["widthPx"] = width_px
        self._props["heightPx"] = height_px
        self._props["points"] = points or []
        self._props["mode"] = mode
        self._props["visibleKinds"] = visible_kinds if visible_kinds is not None else list(ALL_KINDS)
        self._props["editingPointId"] = None
        self.style("height: 70vh")

    def set_points(self, points: list[dict]) -> None:
        self._props["points"] = points
        self.update()

    def set_mode(self, mode: str) -> None:
        self._props["mode"] = mode
        self.update()

    def set_visible_kinds(self, kinds: list[str]) -> None:
        self._props["visibleKinds"] = kinds
        self.update()

    def set_editing_point(self, point_id: str | None) -> None:
        self._props["editingPointId"] = point_id
        self.update()

    def finish_editing(self) -> AwaitableResponse:
        """Commits the in-progress vertex edit and returns the shape's new
        vertices (or None if nothing was being edited)."""
        return self.run_method("finishEditing")
