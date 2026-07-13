# Floor Plan Zones (Polygon Rooms/Desks) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let admins draw a polygon outline for a room or desk on a floor plan (instead of just a dot pin), reshape it later, toggle visibility per pin-kind, and see the office's existing owner on its popup.

**Architecture:** `MapPoint` gains an optional `polygon` JSON column (list of `[x,y]` vertex pairs); no geometry = today's dot pin, unchanged. The custom `FloorPlanLeaflet` Vue component (already wraps raw Leaflet, not `ui.leaflet`) is extended to render polygons, group markers per-kind for visibility toggles, and drive Leaflet.draw's Polygon/Edit tools. All permission/audit/kind plumbing is reused as-is from the existing `MapPoint` subsystem.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async + Alembic, NiceGUI 3.14 (custom Leaflet element, not `ui.leaflet`), Leaflet 1.9.4 + Leaflet.draw 1.0.4 (both already bundled with NiceGUI — no new dependency), pytest + `nicegui.testing.User`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-13-floorplan-zones-design.md` — read it before starting; this plan implements it section by section.
- No new pip or npm dependency — Leaflet.draw ships inside `nicegui`'s own package at `nicegui/elements/leaflet/dist/leaflet-draw/`.
- Existing point pins (no polygon) must keep rendering and behaving exactly as today — every existing floorplan test must keep passing unless this plan explicitly says to change it.
- `manage_floorplans` permission gates every write path (add/edit/delete geometry), exactly like today's pin CRUD.
- Every new/changed Python behavior needs a pytest test. Leaflet.draw's actual browser-side drag/click gestures are not unit-testable (no JS test harness in this repo) — those are covered by the final manual/Playwright smoke test (Task 8), not pytest.
- Follow TDD: write the failing test before the implementation, for every step that touches Python.

---

### Task 1: Data model + migration for `MapPoint.polygon`

**Files:**
- Modify: `not_dot_net/backend/floorplan_models.py`
- Create: `alembic/versions/0019_add_map_point_polygon.py`
- Test: `tests/test_floorplan_models.py`

**Interfaces:**
- Produces: `MapPoint.polygon: list[list[int]] | None` (default `None`) — every later task reads/writes this attribute directly.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_floorplan_models.py`:

```python
async def test_map_point_polygon_round_trips():
    fp = await _create_floor_plan()
    async with session_scope() as session:
        point = MapPoint(
            floor_plan_id=fp.id, label="Room 101", kind="room", x=60, y=80,
            polygon=[[10, 10], [110, 10], [110, 90], [10, 90]],
        )
        session.add(point)
        await session.commit()
        await session.refresh(point)
        assert point.polygon == [[10, 10], [110, 10], [110, 90], [10, 90]]


async def test_map_point_polygon_defaults_to_none():
    fp = await _create_floor_plan()
    async with session_scope() as session:
        point = MapPoint(floor_plan_id=fp.id, label="Plug 1", kind="wall_plug", x=5, y=5)
        session.add(point)
        await session.commit()
        await session.refresh(point)
        assert point.polygon is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_floorplan_models.py -k polygon -v`
Expected: FAIL — `TypeError: MapPoint.__init__() got an unexpected keyword argument 'polygon'`

- [ ] **Step 3: Add the column to the model**

In `not_dot_net/backend/floorplan_models.py`, add `JSON` to the `sqlalchemy` import and add the field to `MapPoint` right after `y`:

```python
from sqlalchemy import ForeignKey, JSON, String, func
```

```python
    y: Mapped[int] = mapped_column()
    polygon: Mapped[list[list[int]] | None] = mapped_column(JSON, nullable=True, default=None)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_floorplan_models.py -v`
Expected: all PASS

- [ ] **Step 5: Write the Alembic migration**

Create `alembic/versions/0019_add_map_point_polygon.py`:

```python
"""Add MapPoint.polygon (JSON list of [x,y] vertices) for zone/room outlines.

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "map_point",
        sa.Column("polygon", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("map_point", "polygon")
```

- [ ] **Step 6: Verify the migration applies cleanly**

Run: `uv run alembic upgrade head` (against a scratch SQLite/Postgres URL if you don't want to touch `dev.db`) then `uv run alembic downgrade -1` then `uv run alembic upgrade head` again.
Expected: no errors in either direction.

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/backend/floorplan_models.py alembic/versions/0019_add_map_point_polygon.py tests/test_floorplan_models.py
git commit -m "feat: add MapPoint.polygon column for drawn zone geometry"
```

---

### Task 2: Service layer — centroid helper, `add_map_point(polygon=...)`, `update_map_point_geometry`

**Files:**
- Modify: `not_dot_net/backend/floorplan_service.py:137-159` (`add_map_point`)
- Test: `tests/test_floorplan_map_points.py`

**Interfaces:**
- Consumes: `MapPoint.polygon` (Task 1).
- Produces:
  - `_polygon_centroid(polygon: list[list[int]]) -> tuple[int, int]`
  - `add_map_point(floor_plan_id, label, kind, x, y, resource_id=None, polygon=None, actor=None) -> MapPoint` (new `polygon` kwarg; when given, `x`/`y` are ignored and replaced with the centroid)
  - `update_map_point_geometry(point_id: uuid.UUID, polygon: list[list[int]], actor=None) -> MapPoint`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_floorplan_map_points.py`:

```python
def test_polygon_centroid_averages_vertices():
    from not_dot_net.backend.floorplan_service import _polygon_centroid

    assert _polygon_centroid([[0, 0], [10, 0], [10, 10], [0, 10]]) == (5, 5)


async def test_add_map_point_with_polygon_computes_centroid(monkeypatch, tmp_path):
    from not_dot_net.backend.floorplan_service import add_map_point

    await _setup_roles()
    admin = await _create_user(role="admin")
    fp = await _create_floor_plan(admin, monkeypatch, tmp_path)

    point = await add_map_point(
        fp.id, "Room 101", "room", 0, 0,
        polygon=[[0, 0], [100, 0], [100, 80], [0, 80]], actor=admin,
    )
    assert point.polygon == [[0, 0], [100, 0], [100, 80], [0, 80]]
    assert (point.x, point.y) == (50, 40)


async def test_add_map_point_without_polygon_defaults_none(monkeypatch, tmp_path):
    from not_dot_net.backend.floorplan_service import add_map_point

    await _setup_roles()
    admin = await _create_user(role="admin")
    fp = await _create_floor_plan(admin, monkeypatch, tmp_path)

    point = await add_map_point(fp.id, "Plug 1", "wall_plug", 5, 5, actor=admin)
    assert point.polygon is None


async def test_update_map_point_geometry_requires_permission(monkeypatch, tmp_path):
    from not_dot_net.backend.floorplan_service import add_map_point, update_map_point_geometry

    await _setup_roles()
    admin = await _create_user(role="admin")
    staff = await _create_user(email="staff2@test.com", role="staff")
    fp = await _create_floor_plan(admin, monkeypatch, tmp_path)
    point = await add_map_point(
        fp.id, "Room 101", "room", 0, 0,
        polygon=[[0, 0], [100, 0], [100, 80], [0, 80]], actor=admin,
    )

    with pytest.raises(PermissionError):
        await update_map_point_geometry(point.id, [[0, 0], [50, 0], [50, 40], [0, 40]], actor=staff)


async def test_update_map_point_geometry_persists_new_shape_and_centroid(monkeypatch, tmp_path):
    from not_dot_net.backend.floorplan_service import (
        add_map_point, list_map_points, update_map_point_geometry,
    )

    await _setup_roles()
    admin = await _create_user(role="admin")
    fp = await _create_floor_plan(admin, monkeypatch, tmp_path)
    point = await add_map_point(
        fp.id, "Room 101", "room", 0, 0,
        polygon=[[0, 0], [100, 0], [100, 80], [0, 80]], actor=admin,
    )

    new_shape = [[0, 0], [40, 0], [40, 40], [0, 40]]
    updated = await update_map_point_geometry(point.id, new_shape, actor=admin)
    assert updated.polygon == new_shape
    assert (updated.x, updated.y) == (20, 20)

    [stored] = await list_map_points(fp.id)
    assert stored.polygon == new_shape
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_floorplan_map_points.py -k "polygon or update_map_point_geometry" -v`
Expected: FAIL — `_polygon_centroid`/`update_map_point_geometry` not defined, `add_map_point()` rejects `polygon=`.

- [ ] **Step 3: Implement in `not_dot_net/backend/floorplan_service.py`**

Add near the top (after `FLOORPLAN_JPEG_QUALITY`):

```python
def _polygon_centroid(polygon: list[list[int]]) -> tuple[int, int]:
    """Average of the vertices, in the same pixel space as x/y — used as the
    marker/tooltip anchor when a point has drawn geometry instead of a plain pin."""
    xs = [v[0] for v in polygon]
    ys = [v[1] for v in polygon]
    return round(sum(xs) / len(xs)), round(sum(ys) / len(ys))
```

Replace `add_map_point` (lines 137-159):

```python
async def add_map_point(
    floor_plan_id: uuid.UUID, label: str, kind: str, x: int, y: int,
    resource_id: uuid.UUID | None = None, polygon: list[list[int]] | None = None,
    actor=None,
) -> MapPoint:
    if actor is not None:
        await check_permission(actor, MANAGE_FLOORPLANS)
    if polygon is not None:
        x, y = _polygon_centroid(polygon)
    async with session_scope() as session:
        point = MapPoint(
            floor_plan_id=floor_plan_id, label=label, kind=kind, x=x, y=y,
            resource_id=resource_id, polygon=polygon,
        )
        session.add(point)
        await session.commit()
        await session.refresh(point)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "floorplan", "add_point",
        actor_id=(actor.id if actor else None),
        target_type="floor_plan", target_id=floor_plan_id,
        detail=f"label={label} kind={kind}",
    )
    return point
```

Add after `delete_map_point` (after line 185, before `nearest_map_point`):

```python
async def update_map_point_geometry(
    point_id: uuid.UUID, polygon: list[list[int]], actor=None,
) -> MapPoint:
    if actor is not None:
        await check_permission(actor, MANAGE_FLOORPLANS)
    x, y = _polygon_centroid(polygon)
    async with session_scope() as session:
        point = await session.get(MapPoint, point_id)
        if point is None:
            raise ValueError(f"Map point {point_id} not found")
        point.polygon = polygon
        point.x = x
        point.y = y
        await session.commit()
        await session.refresh(point)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "floorplan", "edit_point_shape",
        actor_id=(actor.id if actor else None),
        target_type="floor_plan", target_id=point.floor_plan_id,
        detail=f"label={point.label}",
    )
    return point
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_floorplan_map_points.py tests/test_floorplan_service.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/floorplan_service.py tests/test_floorplan_map_points.py
git commit -m "feat: add polygon-aware add_map_point + update_map_point_geometry"
```

---

### Task 3: `_points_payload` — add `id`, `kind`, `polygon` fields

**Files:**
- Modify: `not_dot_net/frontend/floorplan.py:88-102`
- Test: `tests/test_floorplan_ui_helpers.py`

**Interfaces:**
- Consumes: `MapPoint.polygon` (Task 1).
- Produces: `_points_payload(...)` entries now include `"id"` (str), `"kind"` (str), `"polygon"` (list or `None`) — Task 4's JS reads all three.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_floorplan_ui_helpers.py`:

```python
def test_points_payload_includes_id_and_kind():
    from not_dot_net.frontend.floorplan import _points_payload

    point = MapPoint(floor_plan_id=uuid.uuid4(), label="Room 101", kind="room", x=50, y=60)
    payload = _points_payload([point])
    assert payload[0]["id"] == str(point.id)
    assert payload[0]["kind"] == "room"


def test_points_payload_includes_polygon_when_present():
    from not_dot_net.frontend.floorplan import _points_payload

    point = MapPoint(
        floor_plan_id=uuid.uuid4(), label="Room 101", kind="room", x=50, y=60,
        polygon=[[10, 10], [90, 10], [90, 70], [10, 70]],
    )
    payload = _points_payload([point])
    assert payload[0]["polygon"] == [[10, 10], [90, 10], [90, 70], [10, 70]]


def test_points_payload_polygon_defaults_none():
    from not_dot_net.frontend.floorplan import _points_payload

    point = MapPoint(floor_plan_id=uuid.uuid4(), label="Plug 1", kind="wall_plug", x=1, y=1)
    payload = _points_payload([point])
    assert payload[0]["polygon"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -k "includes_id_and_kind or polygon" -v`
Expected: FAIL — `KeyError: 'id'`

- [ ] **Step 3: Implement**

Replace `_points_payload` in `not_dot_net/frontend/floorplan.py:88-102`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/floorplan.py tests/test_floorplan_ui_helpers.py
git commit -m "feat: include id/kind/polygon in floor plan marker payload"
```

---

### Task 4: `FloorPlanLeaflet` — polygon rendering, per-kind layers, Leaflet.draw draw+edit

**Files:**
- Modify: `not_dot_net/frontend/floorplan_leaflet.py` (full rewrite)
- Modify: `not_dot_net/frontend/floorplan_leaflet.js` (full rewrite)
- Create: `tests/test_floorplan_leaflet_element.py`

**Interfaces:**
- Consumes: payload shape from Task 3 (`id`, `kind`, `polygon` per point).
- Produces (Python wrapper API used by Task 5/6/7):
  - `FloorPlanLeaflet(*, image_url, width_px, height_px, points=None, mode="off", visible_kinds=None)`
  - `.set_points(points: list[dict])` (unchanged)
  - `.set_mode(mode: str)` — one of `"off" | "place" | "draw" | "editing"`
  - `.set_visible_kinds(kinds: list[str])`
  - `.set_editing_point(point_id: str | None)`
  - `.finish_editing() -> AwaitableResponse` (awaited, returns `list[list[int]] | None`)
  - `ALL_KINDS: list[str]` = `["room", "desk", "wall_plug", "asset", "other"]`
- Produces (JS events, consumed by Task 5 via `leaflet.on(...)`):
  - `"image-click"` → `{x, y}` (only while `mode == "place"`, unchanged from today)
  - `"zone-drawn"` → `{vertices: [[x,y], ...]}` (a polygon was completed while `mode == "draw"`)
  - `"pin-click"` → `{id: string}` (an existing pin/zone was clicked while `mode == "off"` or `"place"`/`"draw"` on blank canvas is not applicable — see note below)

Note on `interactive`: pins/zones are only Leaflet-interactive (clickable) when `mode == "off"`. In `"place"`/`"draw"`/`"editing"` they're non-interactive, so a click always falls through to the map's own click handler (placing a new pin/zone) or is fully suppressed (`"editing"`) rather than accidentally opening a different pin's popup mid-action.

- [ ] **Step 1: Rewrite `not_dot_net/frontend/floorplan_leaflet.py`**

```python
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
```

- [ ] **Step 2: Rewrite `not_dot_net/frontend/floorplan_leaflet.js`**

```javascript
import { leaflet as L } from "nicegui-leaflet";

function loadStylesheet(href) {
  if (document.querySelector(`link[href="${href}"]`)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    link.onload = resolve;
    link.onerror = reject;
    document.head.appendChild(link);
  });
}

function loadScript(src) {
  if (document.querySelector(`script[src="${src}"]`)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.onload = resolve;
    script.onerror = reject;
    document.head.appendChild(script);
  });
}

const ALL_KINDS = ["room", "desk", "wall_plug", "asset", "other"];

export default {
  template: "<div></div>",
  props: {
    imageUrl: String,
    widthPx: Number,
    heightPx: Number,
    points: Array,
    resourcePath: String,
    mode: { type: String, default: "off" },
    visibleKinds: { type: Array, default: () => [...ALL_KINDS] },
    editingPointId: { type: String, default: null },
  },
  async mounted() {
    await this.$nextTick();
    await loadStylesheet(window.path_prefix + `${this.resourcePath}/leaflet/leaflet.css`);

    // Leaflet.draw's bundled file is a plain script expecting a global `L`
    // (it does `L.Draw = {...}` directly), but NiceGUI's own Leaflet build is
    // an ES module — `L` here is a local import, never attached to `window`.
    // Bridge it explicitly before loading the plugin script.
    window.L = L;
    await loadScript(window.path_prefix + `${this.resourcePath}/leaflet-draw/leaflet.draw.js`);
    await loadStylesheet(window.path_prefix + `${this.resourcePath}/leaflet-draw/leaflet.draw.css`);

    const bounds = [
      [0, 0],
      [this.heightPx, this.widthPx],
    ];

    this.map = L.map(this.$el, {
      crs: L.CRS.Simple,
      minZoom: -5,
      maxZoom: 4,
      zoomSnap: 0.1,
      attributionControl: false,
    });

    L.imageOverlay(this.imageUrl, bounds).addTo(this.map);
    this.map.fitBounds(bounds);

    this.kindGroups = {};
    ALL_KINDS.forEach((kind) => {
      this.kindGroups[kind] = L.layerGroup();
    });
    this.applyVisibleKinds(this.visibleKinds);

    this.shapesById = {};
    this.redrawLayers(this.points);

    this.drawHandler = new L.Draw.Polygon(this.map, { showArea: false, allowIntersection: false });
    this.editHandler = null;

    this.map.on(L.Draw.Event.CREATED, (e) => {
      if (e.layerType !== "polygon") return;
      const vertices = e.layer
        .getLatLngs()[0]
        .map((ll) => [Math.round(ll.lng), Math.round(this.heightPx - ll.lat)]);
      this.$emit("zone-drawn", { vertices });
    });

    this.map.on("click", (e) => {
      if (this.mode !== "place") return;
      this.$emit("image-click", {
        x: e.latlng.lng,
        y: this.heightPx - e.latlng.lat,
      });
    });

    this.observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) this.map.invalidateSize();
    });
    this.observer.observe(this.$el);
  },
  unmounted() {
    this.editHandler?.disable();
    this.drawHandler?.disable();
    this.observer?.disconnect();
    this.map?.remove();
  },
  watch: {
    points: {
      deep: true,
      handler(newPoints) {
        this.redrawLayers(newPoints);
      },
    },
    mode(newMode) {
      this.onModeChange(newMode);
    },
    visibleKinds: {
      deep: true,
      handler(kinds) {
        this.applyVisibleKinds(kinds);
      },
    },
    editingPointId(newId) {
      if (this.editHandler) {
        this.editHandler.disable();
        this.editHandler = null;
      }
      if (!newId) return;
      const layer = this.shapesById[newId];
      if (!layer) return;
      const group = new L.FeatureGroup([layer]);
      this.editHandler = new L.EditToolbar.Edit(this.map, { featureGroup: group });
      this.editHandler.enable();
    },
  },
  methods: {
    applyVisibleKinds(kinds) {
      const visible = new Set(kinds || []);
      Object.entries(this.kindGroups).forEach(([kind, group]) => {
        const shouldShow = visible.has(kind);
        const isShown = this.map.hasLayer(group);
        if (shouldShow && !isShown) group.addTo(this.map);
        if (!shouldShow && isShown) this.map.removeLayer(group);
      });
    },
    onModeChange(newMode) {
      if (newMode === "draw") {
        this.drawHandler.enable();
      } else {
        this.drawHandler.disable();
      }
      this.redrawLayers(this.points);
    },
    redrawLayers(points) {
      Object.values(this.kindGroups).forEach((group) => group.clearLayers());
      this.shapesById = {};
      const interactive = this.mode === "off";

      (points || []).forEach((point) => {
        const isZone = Array.isArray(point.polygon) && point.polygon.length >= 3;
        let layer;
        if (isZone) {
          const latlngs = point.polygon.map(([x, y]) => [this.heightPx - y, x]);
          layer = L.polygon(latlngs, {
            color: point.highlighted ? "black" : point.color || "#1976d2",
            weight: point.highlighted ? 3 : 1,
            fillColor: point.color || "#1976d2",
            fillOpacity: 0.35,
            interactive,
          });
        } else {
          layer = L.circleMarker([this.heightPx - point.y, point.x], {
            radius: 8,
            color: point.highlighted ? "black" : "white",
            weight: point.highlighted ? 2 : 1,
            fillColor: point.color || "#1976d2",
            fillOpacity: 1,
            interactive,
          });
        }
        layer.bindTooltip(point.label, {
          permanent: true,
          direction: "right",
          className: "nicegui-leaflet-pin-label",
        });
        if (interactive) {
          layer.on("click", () => this.$emit("pin-click", { id: point.id }));
        }
        (this.kindGroups[point.kind] || this.kindGroups.other).addLayer(layer);
        this.shapesById[point.id] = layer;
      });
    },
    finishEditing() {
      if (!this.editHandler) return null;
      this.editHandler.save();
      const layer = this.shapesById[this.editingPointId];
      this.editHandler.disable();
      this.editHandler = null;
      if (!layer) return null;
      return layer.getLatLngs()[0].map((ll) => [Math.round(ll.lng), Math.round(this.heightPx - ll.lat)]);
    },
  },
};
```

- [ ] **Step 3: Write the failing Python-wrapper tests**

Create `tests/test_floorplan_leaflet_element.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_floorplan_leaflet_element.py -v`
Expected: all PASS (these only check the Python-side prop plumbing — the JS itself is verified in Task 8's manual smoke test).

- [ ] **Step 5: Run the full floorplan test suite to check for regressions**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -v`
Expected: all PASS unchanged (nothing in this task touched `floorplan.py` yet).

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/frontend/floorplan_leaflet.py not_dot_net/frontend/floorplan_leaflet.js tests/test_floorplan_leaflet_element.py
git commit -m "feat: polygon rendering, per-kind layers, Leaflet.draw draw+edit in FloorPlanLeaflet"
```

---

### Task 5: `floorplan.py` — mode selector, draw/edit routing, zone save flow

This is the largest task: it rewrites the interaction model in `_render_plan_area`, extends `_show_add_pin_dialog` and `_show_pin_actions`, and retires the now-dead `nearest_map_point` hit-testing.

**Files:**
- Modify: `not_dot_net/frontend/floorplan.py` (imports, `_render_floorplan`, `_render_plan_area`, `_show_add_pin_dialog`, `_show_pin_actions`; delete `nearest_map_point` usage)
- Modify: `not_dot_net/backend/floorplan_service.py` (delete `nearest_map_point`)
- Modify: `not_dot_net/frontend/i18n.py` (new keys)
- Modify: `tests/test_floorplan_ui_helpers.py` (existing `place_mode` fixtures + the switch→toggle reproducer test)
- Modify: `tests/test_floorplan_map_points.py` (delete the 3 `nearest_map_point` tests)

**Interfaces:**
- Consumes: `FloorPlanLeaflet` API from Task 4; `update_map_point_geometry` from Task 2.
- Produces: `state` dict gains `"editing_point_id"` key (default `None`); `"place_mode"` becomes a 3-way string `"off" | "place" | "draw"` (was `bool`).

- [ ] **Step 1: Confirm `nearest_map_point` has no other callers**

Run: `grep -rn "nearest_map_point" --include=*.py .` (excluding `.worktrees`/`__pycache__`)
Expected: only `not_dot_net/backend/floorplan_service.py` (definition), `not_dot_net/frontend/floorplan.py` (import + one call site), `tests/test_floorplan_map_points.py` (3 tests) — confirming it's safe to delete once this task stops calling it.

- [ ] **Step 2: Add new i18n keys**

In `not_dot_net/frontend/i18n.py`, in the `"en"` block right after line 296 (`"floorplan_place_pin_mode": "Place pin",`):

```python
        "floorplan_mode_off": "Off",
        "floorplan_draw_zone_mode": "Draw zone",
        "floorplan_edit_shape": "Edit shape",
        "floorplan_finish_edit": "Done",
        "floorplan_shape_updated": "Shape updated",
```

In the `"fr"` block right after the French `"floorplan_place_pin_mode": "Placer un point",` line:

```python
        "floorplan_mode_off": "Désactivé",
        "floorplan_draw_zone_mode": "Dessiner une zone",
        "floorplan_edit_shape": "Modifier la forme",
        "floorplan_finish_edit": "Terminer",
        "floorplan_shape_updated": "Forme mise à jour",
```

- [ ] **Step 3: Update existing `place_mode` fixtures from bool to string**

In `tests/test_floorplan_ui_helpers.py`, replace every `"place_mode": False` with `"place_mode": "off"` (lines 104, 222, 251, 304, 399, 432, 483) and every `"place_mode": True` with `"place_mode": "place"` (line 352, and inside `test_place_pin_mode_persists_across_pin_area_rerender` at line 111 — see Step 4 for that test's full rewrite). Also add `"editing_point_id": None` to every one of these `state = {...}` dict literals, since `_render_plan_area` will read that key.

- [ ] **Step 4: Rewrite the switch→toggle reproducer test**

Replace `test_place_pin_mode_persists_across_pin_area_rerender` (lines 86-124) in `tests/test_floorplan_ui_helpers.py`:

```python
async def test_place_pin_mode_persists_across_pin_area_rerender(
    user: User, monkeypatch, tmp_path
) -> None:
    """Reproducer: _render_plan_area used to hardcode the mode selector back
    to "off" every time it re-rendered (e.g. right after a pin was added),
    forcing an admin placing several pins in a row to re-toggle it before
    every click. The toggle's initial value must come from persisted state."""
    from not_dot_net.frontend.floorplan import _render_plan_area
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)

    admin = await _make_admin()
    plan = await create_floor_plan("Reproducer Plan", _make_image_bytes(), actor=admin)

    @ui.page("/floorplan-rerender-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": "off", "editing_point_id": None}
        await _render_plan_area(area, state, admin, True)

        async def simulate_pin_added():
            # Mirrors what _show_add_pin_dialog's do_save does: the toggle
            # was already switched to "place" by the admin, then a pin gets
            # added and the plan area re-renders.
            state["place_mode"] = "place"
            await _render_plan_area(area, state, admin, True)
            with area:
                ui.label("rerender-complete")

        ui.button("simulate-pin-added", on_click=simulate_pin_added)

    await user.open("/floorplan-rerender-test")
    user.find("simulate-pin-added").click()
    await user.should_see("rerender-complete")

    toggles = list(user.find(kind=ui.toggle).elements)
    assert len(toggles) == 1
    assert toggles[0].value == "place"
```

- [ ] **Step 5: Delete the 3 `nearest_map_point` tests**

In `tests/test_floorplan_map_points.py`, delete `test_nearest_map_point_finds_closest_within_radius`, `test_nearest_map_point_returns_none_outside_radius`, and `test_nearest_map_point_handles_empty_list` (lines 84-106).

- [ ] **Step 6: Delete `nearest_map_point` from the service**

In `not_dot_net/backend/floorplan_service.py`, delete the `nearest_map_point` function (lines 188-197).

- [ ] **Step 7: Write the new/changed `floorplan.py` tests**

Add to `tests/test_floorplan_ui_helpers.py`:

```python
async def test_show_add_pin_dialog_with_polygon_persists_geometry(
    user: User, monkeypatch, tmp_path
) -> None:
    from nicegui import ElementFilter
    from nicegui import ui as nicegui_ui

    from not_dot_net.backend.floorplan_service import list_map_points
    from not_dot_net.frontend.floorplan import _show_add_pin_dialog
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()
    plan = await create_floor_plan("Zone Plan", _make_image_bytes(), actor=admin)
    polygon = [[0, 0], [50, 0], [50, 40], [0, 40]]

    @ui.page("/add-zone-dialog-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": "draw", "editing_point_id": None}
        await _show_add_pin_dialog(area, state, admin, True, plan.id, 25, 20, polygon=polygon)

    await user.open("/add-zone-dialog-test")
    with user.client:
        label_input = next(iter(ElementFilter(kind=nicegui_ui.input)))
        label_input.value = "Room Zone"

    user.find(t("save")).click()
    await user.should_see(t("floorplan_pin_added"))

    points = await list_map_points(plan.id)
    added = next(p for p in points if p.label == "Room Zone")
    assert added.polygon == polygon
    assert (added.x, added.y) == (25, 20)


def test_should_place_pin_requires_admin_and_place_mode():
    from not_dot_net.frontend.floorplan import _should_place_pin

    assert _should_place_pin(True, "place") is True
    assert _should_place_pin(False, "place") is False
    assert _should_place_pin(True, "draw") is False
    assert _should_place_pin(True, "off") is False


def test_should_draw_zone_requires_admin_and_draw_mode():
    from not_dot_net.frontend.floorplan import _should_draw_zone

    assert _should_draw_zone(True, "draw") is True
    assert _should_draw_zone(False, "draw") is False
    assert _should_draw_zone(True, "place") is False


async def test_pin_actions_shows_edit_shape_button_for_zone(user: User, monkeypatch, tmp_path) -> None:
    from not_dot_net.backend.floorplan_service import add_map_point
    from not_dot_net.frontend.floorplan import _show_pin_actions
    from not_dot_net.frontend.floorplan_leaflet import FloorPlanLeaflet
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()
    plan = await create_floor_plan("Zone Popup Plan", _make_image_bytes(), actor=admin)
    point = await add_map_point(
        plan.id, "Room Z", "room", 0, 0,
        polygon=[[0, 0], [40, 0], [40, 30], [0, 30]], actor=admin,
    )

    @ui.page("/pin-actions-edit-shape-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": "off", "editing_point_id": None}
        leaflet = FloorPlanLeaflet(image_url="x", width_px=100, height_px=100)
        await _show_pin_actions(area, state, admin, True, point, leaflet=leaflet)

    await user.open("/pin-actions-edit-shape-test")
    await user.should_see(t("floorplan_edit_shape"))


async def test_pin_actions_hides_edit_shape_button_for_plain_pin(user: User, monkeypatch, tmp_path) -> None:
    from not_dot_net.backend.floorplan_service import add_map_point
    from not_dot_net.frontend.floorplan import _show_pin_actions
    from not_dot_net.frontend.floorplan_leaflet import FloorPlanLeaflet
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs
    import pytest

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()
    plan = await create_floor_plan("Plain Pin Plan", _make_image_bytes(), actor=admin)
    point = await add_map_point(plan.id, "Plug X", "wall_plug", 5, 5, actor=admin)

    @ui.page("/pin-actions-no-edit-shape-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": "off", "editing_point_id": None}
        leaflet = FloorPlanLeaflet(image_url="x", width_px=100, height_px=100)
        await _show_pin_actions(area, state, admin, True, point, leaflet=leaflet)

    await user.open("/pin-actions-no-edit-shape-test")
    with pytest.raises(AssertionError):
        await user.should_see(t("floorplan_edit_shape"))
```

- [ ] **Step 8: Run tests to verify the new ones fail**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -k "add_pin_dialog_with_polygon or should_place_pin or should_draw_zone or edit_shape" -v`
Expected: FAIL — `_should_place_pin`/`_should_draw_zone` not defined, `_show_add_pin_dialog` rejects `polygon=`, `_show_pin_actions` rejects `leaflet=`.

- [ ] **Step 9: Implement — imports**

In `not_dot_net/frontend/floorplan.py`, change the `floorplan_service` import block (lines 12-21):

```python
from not_dot_net.backend.floorplan_service import (
    add_map_point,
    create_floor_plan,
    delete_floor_plan,
    delete_map_point,
    get_floor_plan_image,
    list_floor_plans,
    list_map_points,
    update_map_point_geometry,
)
```

- [ ] **Step 10: Implement — routing helper functions**

Add near `_resource_picker_visible` (note: exact line numbers below have shifted a few lines down from the numbers in this repo's current `floorplan.py`, since Task 3 added lines to `_points_payload` earlier in the file — anchor on the function names/content shown, not the original line count):

```python
def _should_place_pin(is_admin: bool, mode: str) -> bool:
    return is_admin and mode == "place"


def _should_draw_zone(is_admin: bool, mode: str) -> bool:
    return is_admin and mode == "draw"
```

- [ ] **Step 11: Implement — `_render_floorplan`'s initial state**

Change the `state = {...}` initialization line (find `"place_mode": False` in `_render_floorplan`):

```python
        state = {"selected": plans[0], "highlight_id": None, "place_mode": "off", "editing_point_id": None}
```

- [ ] **Step 12: Implement — rewrite `_render_plan_area`**

Replace `_render_plan_area` in full (find it by name — line numbers have shifted since Task 3's edit):

```python
async def _render_plan_area(plan_area, state, user, is_admin):
    plan_area.clear()
    plan = state["selected"]
    image_bytes = await get_floor_plan_image(plan.id)
    points = await list_map_points(plan.id)
    points_by_id = {str(p.id): p for p in points}
    editing_id = state.get("editing_point_id")

    with plan_area:
        if image_bytes is None:
            ui.label(t("floorplan_none")).classes("text-grey")
            return

        # Container created before `leaflet` so its controls render above the
        # map (DOM position = element-creation time, not content-fill time —
        # see the switcher-position gotcha this file already learned once).
        # Its content is filled in below, after `leaflet` exists, so the
        # closures here can reference it without any forward-declaration.
        controls_row = ui.column().classes("w-full")

        leaflet_mode = "editing" if editing_id is not None else state.get("place_mode", "off")
        leaflet = FloorPlanLeaflet(
            image_url=_floorplan_image_data_uri(image_bytes),
            width_px=plan.width_px, height_px=plan.height_px,
            points=_points_payload(points, state["highlight_id"]),
            mode=leaflet_mode,
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
```

- [ ] **Step 13: Implement — extend `_show_add_pin_dialog` for `polygon`**

Change the signature and `do_save` in `_show_add_pin_dialog` (find it by name — line numbers have shifted since Task 3's edit):

```python
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
```

(Only the function signature line and the `await add_map_point(...)` call inside `do_save` change — everything else in the function body is unchanged.)

- [ ] **Step 14: Implement — extend `_show_pin_actions` for the Edit-shape button**

Change the signature and body of `_show_pin_actions` (find it by name — line numbers have shifted since Task 3's edit):

```python
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

                ui.button(t("delete"), icon="delete", on_click=do_delete).props("color=negative")
    dialog.open()
```

- [ ] **Step 15: Run the full floorplan test suite**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py tests/test_floorplan_map_points.py tests/test_floorplan_models.py tests/test_floorplan_service.py tests/test_floorplan_leaflet_element.py -v`
Expected: all PASS

- [ ] **Step 16: Commit**

```bash
git add not_dot_net/frontend/floorplan.py not_dot_net/backend/floorplan_service.py not_dot_net/frontend/i18n.py tests/test_floorplan_ui_helpers.py tests/test_floorplan_map_points.py
git commit -m "feat: draw/edit zone mode selector, save flow, and edit-shape action in floor plan UI"
```

---

### Task 6: Per-kind layer toggle checkboxes

**Files:**
- Modify: `not_dot_net/frontend/floorplan.py` (`_render_plan_area`)
- Test: `tests/test_floorplan_ui_helpers.py`

**Interfaces:**
- Consumes: `FloorPlanLeaflet.set_visible_kinds` (Task 4), `PIN_KINDS`/`_KIND_COLOR`/`_pin_kind_select_options` (existing).
- Produces: `state` dict gains `"visible_kinds"` key (default: all of `PIN_KINDS`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_floorplan_ui_helpers.py`:

```python
async def test_render_plan_area_shows_kind_toggle_checkboxes(user: User, monkeypatch, tmp_path) -> None:
    from not_dot_net.frontend.floorplan import PIN_KINDS, _render_plan_area
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()
    plan = await create_floor_plan("Layer Toggle Plan", _make_image_bytes(), actor=admin)

    @ui.page("/floorplan-layer-toggles-test")
    async def page():
        area = ui.column()
        state = {
            "selected": plan, "highlight_id": None, "place_mode": "off",
            "editing_point_id": None,
        }
        await _render_plan_area(area, state, admin, True)

    await user.open("/floorplan-layer-toggles-test")
    for kind in PIN_KINDS:
        await user.should_see(t(f"kind_{kind}"))

    checkboxes = list(user.find(kind=ui.checkbox).elements)
    assert len(checkboxes) == len(PIN_KINDS)
    assert all(cb.value is True for cb in checkboxes)


async def test_unchecking_a_kind_updates_visible_kinds_state(user: User, monkeypatch, tmp_path) -> None:
    from nicegui import ElementFilter

    from not_dot_net.frontend.floorplan import _render_plan_area
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()
    plan = await create_floor_plan("Layer Toggle Plan 2", _make_image_bytes(), actor=admin)

    state = {
        "selected": plan, "highlight_id": None, "place_mode": "off",
        "editing_point_id": None,
    }

    @ui.page("/floorplan-layer-toggles-uncheck-test")
    async def page():
        area = ui.column()
        await _render_plan_area(area, state, admin, True)

    await user.open("/floorplan-layer-toggles-uncheck-test")
    with user.client:
        desk_checkbox = next(
            cb for cb in ElementFilter(kind=ui.checkbox) if cb.text == t("kind_desk")
        )
        desk_checkbox.value = False

    assert "desk" not in state["visible_kinds"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -k "kind_toggle or visible_kinds" -v`
Expected: FAIL — no checkboxes rendered.

- [ ] **Step 3: Implement**

In `not_dot_net/frontend/floorplan.py`, inside `_render_plan_area`, pass `visible_kinds` to the `FloorPlanLeaflet(...)` constructor call and add the checkbox row to `controls_row`. The updated relevant portion:

```python
        leaflet_mode = "editing" if editing_id is not None else state.get("place_mode", "off")
        visible_kinds = state.setdefault("visible_kinds", list(PIN_KINDS))
        leaflet = FloorPlanLeaflet(
            image_url=_floorplan_image_data_uri(image_bytes),
            width_px=plan.width_px, height_px=plan.height_px,
            points=_points_payload(points, state["highlight_id"]),
            mode=leaflet_mode,
            visible_kinds=visible_kinds,
        )
        if editing_id is not None:
            leaflet.set_editing_point(editing_id)

        with controls_row:
            if editing_id is not None:
                ...  # unchanged from Task 5
            elif is_admin:
                ...  # unchanged from Task 5

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
```

Note the `kind=kind` default-arg capture in `on_kind_toggle` — every closure created inside a `for` loop in this codebase must capture its loop variable this way (a documented project gotcha), otherwise every checkbox's handler would close over the same final `kind` value.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/floorplan.py tests/test_floorplan_ui_helpers.py
git commit -m "feat: per-kind layer visibility checkboxes on floor plan"
```

---

### Task 7: Occupant name display on office pins/zones

**Files:**
- Modify: `not_dot_net/frontend/floorplan.py` (`_show_pin_actions`)
- Test: `tests/test_floorplan_ui_helpers.py`

**Interfaces:**
- Consumes: `resolve_user_names` (`not_dot_net/backend/db.py:105`, already exists).
- Produces: nothing new consumed by later tasks — this is the last feature task.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_floorplan_ui_helpers.py`:

```python
async def test_pin_actions_shows_owner_name_for_office_with_owner(user: User, monkeypatch, tmp_path) -> None:
    from not_dot_net.backend.booking_service import create_resource
    from not_dot_net.backend.floorplan_service import add_map_point
    from not_dot_net.frontend.floorplan import _show_pin_actions
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()
    owner = await _create_staff_user(email="owner-name-test@test.com")
    resource = await create_resource("Room 601", "office", location="Palaiseau",
                                     owner_user_id=owner.id, actor=admin)
    plan = await create_floor_plan("Owner Name Plan", _make_image_bytes(), actor=admin)
    point = await add_map_point(plan.id, "Room 601", "room", 50, 50,
                                resource_id=resource.id, actor=admin)

    @ui.page("/pin-actions-owner-name-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": "off", "editing_point_id": None}
        await _show_pin_actions(area, state, admin, False, point)

    await user.open("/pin-actions-owner-name-test")
    expected_name = owner.full_name or owner.email
    await user.should_see(expected_name)


async def test_pin_actions_shows_no_owner_for_unowned_office(user: User, monkeypatch, tmp_path) -> None:
    from not_dot_net.backend.booking_service import create_resource
    from not_dot_net.backend.floorplan_service import add_map_point
    from not_dot_net.frontend.floorplan import _show_pin_actions
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()
    resource = await create_resource("Room 602", "office", location="Palaiseau", actor=admin)
    plan = await create_floor_plan("Unowned Office Plan", _make_image_bytes(), actor=admin)
    point = await add_map_point(plan.id, "Room 602", "room", 50, 50,
                                resource_id=resource.id, actor=admin)

    @ui.page("/pin-actions-no-owner-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": "off", "editing_point_id": None}
        await _show_pin_actions(area, state, admin, False, point)

    await user.open("/pin-actions-no-owner-test")
    await user.should_see(t("no_owner"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -k owner_name -v`
Expected: FAIL — owner name/`no_owner` text not present in the popup.

- [ ] **Step 3: Implement**

In `not_dot_net/frontend/floorplan.py`, add the import:

```python
from not_dot_net.backend.db import User, resolve_user_names
```

(`User` is already imported on line 10 — just add `resolve_user_names` to that import line.)

In `_show_pin_actions` (as it stands after Task 5's edits), find this block:

```python
        if is_office:
            await _render_office_availability_section(dialog, plan_area, state, user, is_admin, resource)
```

and replace it with:

```python
        if is_office:
            owner_names = await resolve_user_names([resource.owner_user_id])
            owner_label = owner_names.get(resource.owner_user_id, t("no_owner"))
            ui.label(f"{t('resource_owner')}: {owner_label}").classes("text-sm")
            await _render_office_availability_section(dialog, plan_area, state, user, is_admin, resource)
```

(This replaces the single existing `await _render_office_availability_section(...)` line with the two lines above plus the original call.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/floorplan.py tests/test_floorplan_ui_helpers.py
git commit -m "feat: show office owner name on floor plan pin/zone popup"
```

---

### Task 8: Full suite verification + manual smoke test

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest`
Expected: all tests pass, exit code 0. Read the actual pass count — don't infer from a partial grep.

- [ ] **Step 2: Manual/Playwright smoke test**

Start the dev server (`uv run python -m not_dot_net.cli serve --host localhost --port 8088`), log in as an admin, go to the Floor Plan tab, and walk through:

1. Switch mode to "Draw zone", draw a polygon for a room, save it with a label/kind — confirm it renders as a filled shape, not a dot.
2. Click the drawn zone — confirm the popup opens with the label, kind, and (if linked to an office resource) the owner name.
3. Click "Edit shape" — confirm the map shows draggable vertex handles, drag one, click "Done" — confirm the shape's new outline persists after reload.
4. Switch mode to "Draw zone" again, draw a desk-kind zone — confirm size/orientation is preserved (a non-square/rotated shape stays non-square/rotated).
5. Uncheck the "Desk" layer checkbox — confirm desk zones/pins disappear from the map; re-check to confirm they reappear.
6. Confirm existing plain pins (wall_plug/asset/other, or any pre-existing room/desk pin never drawn as a zone) still render and behave exactly as before (click opens the same popup, admin can still delete them).
7. Delete a zone via the popup's Delete button — confirm it's removed.

This is the step that catches real browser/JS bugs unit tests can't (per the project's own prior experience: a live smoke test caught 2 real bugs the last time this floor-plan component was touched). Do not report this task done without actually running it.

- [ ] **Step 3: Fix any bugs found, with a regression test where possible**

If the smoke test surfaces a bug, write a reproducer test first (per project convention) before fixing it, then re-run the full suite.

- [ ] **Step 4: Final commit (only if Step 3 produced changes)**

```bash
git add -A
git commit -m "fix: address issues found in floor plan zones smoke test"
```
