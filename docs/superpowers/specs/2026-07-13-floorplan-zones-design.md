# Floor plan zones (polygon rooms/desks) — design

## Motivation

Floor plan pins (`MapPoint`) currently render as fixed-size dots regardless of what
they represent. A dot works fine for a wall plug, but doesn't convey a room's actual
footprint or a desk's size/orientation. This adds an optional polygon shape to map
points, so admins can draw the real outline of an office or desk instead of (or in
addition to) dropping a pin, plus per-kind layer toggles and surfacing the existing
office-owner data on the pin popup.

## Scope

In scope:
- Optional polygon geometry on `MapPoint`, coexisting with plain point pins.
- Admin drawing (freeform polygon) and in-place reshaping (drag vertices) via
  leaflet-draw, already bundled with NiceGUI.
- Per-kind layer visibility toggles (room/desk/wall_plug/asset/other).
- Click-to-open-actions works for polygons (not just circular-radius pin hits).
- Surfacing the office resource's existing `owner_user_id` as a display name on
  the pin/zone popup.

Out of scope (explicitly not doing):
- Forcing existing pins to become zones, or any migration/backfill of geometry.
- Rectangle-with-rotation drawing tool / any new JS dependency beyond leaflet-draw.
- A new "assigned occupant" concept — offices already have `Resource.owner_user_id`;
  this only displays it.
- Point-in-polygon math on the server — hit-testing is delegated to Leaflet's own
  per-shape click events (see "Hit-testing" below).

## Data model

Add one nullable column to `MapPoint` (`not_dot_net/backend/floorplan_models.py`):

```python
polygon: Mapped[list[list[int]] | None] = mapped_column(JSON, nullable=True, default=None)
```

- `polygon` is a list of `[x, y]` vertex pairs, in the same floor-plan pixel space as
  the existing `x`/`y` columns.
- `x`/`y` keep their current meaning (marker/tooltip anchor) but become the
  **centroid** of the polygon when one exists. The centroid is computed once at
  save time (by the service layer, not the frontend) and is not independently
  editable — reshaping the polygon recomputes it.
- `polygon is None` → renders exactly as today, a simple dot pin. No behavior change
  for any existing row.
- `kind`, `resource_id`, permission checks (`manage_floorplans`), and audit logging
  are unchanged and apply identically whether or not a point has a polygon.

**Migration**: one Alembic revision adding the nullable `polygon` column to
`map_point`. No backfill — existing rows stay `NULL`.

## Editing UX (admin only, `manage_floorplans` permission)

The existing "Place pin mode" switch (`frontend/floorplan.py:182`) becomes a 3-way
mode selector — **Off / Place pin / Draw zone** — implemented as a single control
(e.g. `ui.toggle` with three options) rather than independent switches, so the two
modes can't be active simultaneously.

**Drawing a zone**: in Draw-zone mode, clicking the map starts leaflet-draw's
Polygon tool. The admin clicks each corner and closes the shape (click the first
vertex again, or double-click). On completion, the JS emits the vertex list; Python
opens the *same* add-pin dialog used today (label, kind, resource picker), now
also carrying geometry. Saving computes the centroid and calls
`add_map_point(..., polygon=vertices)`.

**Reshaping an existing zone**: the pin/zone popup (`_show_pin_actions`) gains an
"Edit shape" button, shown only when `point.polygon is not None` and the viewer is
admin. Clicking it enables leaflet-draw's Edit mode scoped to just that one polygon
layer (drag individual vertices), with a floating Done/Cancel control. Done sends
the updated vertex list to a new service function:

```python
async def update_map_point_geometry(point_id: uuid.UUID, polygon: list[list[int]], actor=None) -> MapPoint
```

permission-checked (`manage_floorplans`), audit-logged as `edit_point_shape`,
recomputing and persisting the centroid.

**Deleting** a zone reuses the existing `delete_map_point` control unchanged — a
zone is still just one `MapPoint` row.

## Rendering, layer toggles, and click hit-testing

**Rendering** (`frontend/floorplan_leaflet.js`): the marker payload gains an
optional `polygon` field per point. Points with geometry render as `L.polygon`
(filled, colored by kind via the existing `_KIND_COLOR` map, same
highlight-on-select styling as today's pins); points without geometry keep
rendering as `L.circleMarker`. Both live in the same per-kind layer group.

**Layer toggles**: one checkbox per kind (room / desk / wall_plug / asset / other),
built as ordinary NiceGUI checkboxes above the map (not Leaflet's built-in layer
control, to stay translatable and visually consistent with the rest of the page).
Each kind's pins + zones belong to one `L.layerGroup`; toggling a checkbox
adds/removes that group from the map. All groups visible by default.

**Click hit-testing** — a necessary behavior change: today, every map click reports
raw `(x, y)`, and Python finds the nearest pin within a fixed radius
(`nearest_map_point`, `backend/floorplan_service.py:188`). That radius-based
approach doesn't generalize to an office-sized polygon — clicking in the middle of
a large room could be far from its centroid and miss entirely. So: pins/zones
become Leaflet-interactive (`interactive: true`, currently `false` for
circleMarkers) and each shape's own click handler emits its own point ID directly
— Leaflet already knows which shape was clicked, so no server-side
point-in-polygon math is needed. The map-wide click handler now fires only in
Place-pin/Draw-zone mode, for placing new things on blank canvas.
`nearest_map_point` becomes dead code for the click-routing path; the
implementation plan should confirm it has no other callers before removing it.

## Occupant name display

In `_show_pin_actions`, when `is_office` is true, resolve `resource.owner_user_id`
via the existing `resolve_user_names` helper (`backend/db.py:105`) and show it next
to the office label (e.g. "Owner: Jane Doe"). If `owner_user_id` is `None`, show the
existing `t("no_owner")` string already used in the resource-edit dialog
(`frontend/bookings.py:739`). No new field, no new lookup — this only surfaces data
that already exists on `Resource`.

## Testing

TDD per project convention (`uv run pytest`, `nicegui.testing.User`):

- **Model**: `MapPoint` round-trips a `polygon` value.
- **Service**: `add_map_point(..., polygon=...)` persists geometry + computed
  centroid; `update_map_point_geometry` is permission-gated, persists, and
  audit-logs; deleting a zone behaves like deleting a pin.
- **Frontend/service integration**: rendering payload includes `polygon` when
  present; occupant name renders correctly for offices with/without an owner; each
  kind-toggle checkbox is present and wired.

**Caveat**: the actual drawing/editing gestures (leaflet-draw's Polygon/Edit tools
running in the browser) aren't exercisable through `nicegui.testing.User` — those
are JS-side interactions with the Leaflet map instance. Per the pattern from the
prior floor-plan feature (a live Playwright smoke test caught two real bugs unit
tests couldn't), this feature gets a manual/Playwright smoke test of
draw → save → reshape → delete before being called done, not just the pytest
suite.

## Alternatives considered

**Separate `MapZone` table** instead of extending `MapPoint`: cleaner separation of
"point" vs "area" as concepts, but duplicates most of what `MapPoint` already does
(resource-linking, permission checks, audit entries, kind coloring, click routing)
for what is conceptually the same "a labeled thing on the floor plan." Rejected in
favor of extending `MapPoint`, since pins and zones were explicitly decided to
coexist as variants of the same thing, not two different subsystems.

**Rectangle + rotation handle** instead of freeform polygon drawing: more
convenient for the common rectangular case, but leaflet-draw's built-in Rectangle
tool doesn't support rotation, which would require an additional plugin (e.g.
leaflet.pm) just for orientation. Freeform polygon (already bundled via
leaflet-draw) captures arbitrary size and orientation for free, at the cost of a
few more clicks per shape.
