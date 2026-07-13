"""Pannable/zoomable floor-plan image, backed by Leaflet's CRS.Simple mode.

NiceGUI's own `ui.leaflet` cannot be configured for CRS.Simple: its `options`
dict is JSON-serialized to the client, so `L.CRS.Simple` (a JS object with
project/unproject/scale methods) collapses to the literal string
"L.CRS.Simple" — which crashes Leaflet's `setView` at construction time. This
element hardcodes `crs: L.CRS.Simple` in its own JS instead, reusing NiceGUI's
already-bundled Leaflet assets (no extra download, no CDN).
"""

from pathlib import Path

import nicegui.elements.leaflet as _ng_leaflet
from nicegui.element import Element

_LEAFLET_DIST = Path(_ng_leaflet.__file__).parent / "dist"
_HERE = Path(__file__).parent


class FloorPlanLeaflet(
    Element,
    component=_HERE / "floorplan_leaflet.js",
    esm={"nicegui-leaflet": _LEAFLET_DIST},
    default_classes="w-full border rounded",
):
    def __init__(
        self, *, image_url: str, width_px: int, height_px: int, points: list[dict] | None = None,
    ) -> None:
        super().__init__()
        self.add_resource(_LEAFLET_DIST)
        self._props["imageUrl"] = image_url
        self._props["widthPx"] = width_px
        self._props["heightPx"] = height_px
        self._props["points"] = points or []
        self.style("height: 70vh")

    def set_points(self, points: list[dict]) -> None:
        self._props["points"] = points
        self.update()
