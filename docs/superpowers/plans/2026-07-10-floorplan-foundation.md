# Floor Plan Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin upload a floor-plan image and place labeled pins on it by clicking; any user can view the floor plan and click a pin to see its label. No booking wiring, no wall-plug network schema, no Resource linkage yet — those are follow-up phases once this foundation ships.

**Architecture:** Two new SQLAlchemy tables (`FloorPlan`, `MapPoint`) behind a `floorplan_service.py` CRUD layer (mirrors `booking_service.py`: permission checks, audit logging, `session_scope()`). A new `floorplan.py` frontend page renders the plan image via `ui.interactive_image` (gives image-pixel click coordinates + an SVG overlay — not `ui.leaflet`; see Global Constraints) and is wired into `shell.py` as a new tab.

**Tech Stack:** NiceGUI `ui.interactive_image` (image-pixel-coordinate clicks + raw SVG overlay), Pillow for image validation/resize, SQLAlchemy 2.x async, Alembic migration, existing `permissions.py`/`audit.py` patterns.

## Global Constraints

- **Do not use `ui.leaflet` with `CRS.Simple` for this feature.** Verified against the installed NiceGUI source (`.venv/lib/python3.13/site-packages/nicegui/elements/leaflet/leaflet.js`): the `options` dict passed to `ui.leaflet(options=...)` is spread directly into `L.map(el, {...options})` as plain JSON — there is no expression-eval path for construction-time options (only `run_map_method`/`run_layer_method` support the `:`-prefixed JS-expression convention, and only for post-init calls). A string like `"L.CRS.Simple"` would land as a literal string, not the actual Leaflet CRS object, so panning/zooming a non-geographic floor plan would be broken. `ui.interactive_image` has no such gap: it natively reports `image_x`/`image_y` in the image's own pixel space and accepts a raw SVG overlay sized to match — exactly what's needed here, with no workaround required.
- Follow existing model style: `MappedAsDataclass, Base, kw_only=True`, required fields first, `id` with `default_factory=uuid.uuid4`, `created_at` with `server_default=func.now(), default=None` (see `not_dot_net/backend/booking_models.py`).
- Follow existing service style: `session_scope()` context manager, `check_permission(actor, PERM)` when `actor is not None`, `log_audit(...)` after every mutation (see `not_dot_net/backend/booking_service.py`).
- New permission: `manage_floorplans` — registered via `permission(...)` the same way `MANAGE_BOOKINGS` is in `booking_service.py`. No extra wiring needed for it to appear in the role editor — `admin_roles.py` reads the global registry dynamically.
- Image handling mirrors `not_dot_net/backend/profile_photo.py`: validate via Pillow, `ImageOps.exif_transpose`, resize with `Image.thumbnail`, re-encode — never trust/store the uploaded bytes verbatim.
- Files on disk under `Path(os.environ.get("NDN_DATA_DIR", "data")) / "floorplans"`, same root-env-var convention as `UPLOAD_ROOT` in `workflow_service.py`.
- All new models must be registered in **both** `not_dot_net/backend/db.py::create_db_and_tables()` and `tests/conftest.py::setup_db` (both currently list `booking_models`, `workflow_models`, etc. explicitly — missing either one breaks dev-mode or tests respectively).
- Tab is visible to every logged-in user (same as `bookings_label`/`pages_label` in `shell.py` — not gated behind a permission), with admin-only controls (upload, place-pin, delete) gated behind `manage_floorplans` inside the page, same pattern as `is_admin` in `bookings.py`.

---

### Task 1: Data model + migration

**Files:**
- Create: `not_dot_net/backend/floorplan_models.py`
- Create: `alembic/versions/0017_add_floorplan.py`
- Modify: `not_dot_net/backend/db.py` (register models in `create_db_and_tables()`)
- Modify: `tests/conftest.py` (register models in `setup_db`)
- Test: `tests/test_floorplan_models.py`

**Interfaces:**
- Produces: `FloorPlan(name, image_path, width_px, height_px, id, active=True, created_at)`, `MapPoint(floor_plan_id, label, kind, x, y, id, resource_id=None, created_at)` — both importable from `not_dot_net.backend.floorplan_models`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_floorplan_models.py
import uuid

import pytest
from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.floorplan_models import FloorPlan, MapPoint


async def _create_floor_plan(**kwargs) -> FloorPlan:
    defaults = {"name": "Building A - Floor 1", "image_path": "/data/floorplans/x.jpg",
                "width_px": 1000, "height_px": 800}
    defaults.update(kwargs)
    async with session_scope() as session:
        fp = FloorPlan(**defaults)
        session.add(fp)
        await session.commit()
        await session.refresh(fp)
        return fp


async def test_create_floor_plan_defaults():
    fp = await _create_floor_plan()
    assert fp.active is True
    assert fp.created_at is not None


async def test_map_point_cascades_on_floor_plan_delete():
    fp = await _create_floor_plan()
    async with session_scope() as session:
        point = MapPoint(floor_plan_id=fp.id, label="Plug 12", kind="wall_plug", x=100, y=200)
        session.add(point)
        await session.commit()

    async with session_scope() as session:
        stored = await session.get(FloorPlan, fp.id)
        await session.delete(stored)
        await session.commit()

    async with session_scope() as session:
        remaining = (await session.execute(select(MapPoint))).scalars().all()
    assert remaining == []


async def test_floor_plan_name_is_unique():
    await _create_floor_plan(name="Dup")
    with pytest.raises(Exception):
        await _create_floor_plan(name="Dup")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_floorplan_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'not_dot_net.backend.floorplan_models'`

- [ ] **Step 3: Write the model**

```python
# not_dot_net/backend/floorplan_models.py
"""Floor plan models — an uploaded plan image and the labeled pins on it."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base


class FloorPlan(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "floor_plan"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    image_path: Mapped[str] = mapped_column(String(500))
    width_px: Mapped[int] = mapped_column()
    height_px: Mapped[int] = mapped_column()
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)


class MapPoint(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "map_point"

    floor_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("floor_plan.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30))  # "room" | "desk" | "wall_plug" | "asset" | "other"
    x: Mapped[int] = mapped_column()
    y: Mapped[int] = mapped_column()
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resource.id", ondelete="SET NULL"), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)
```

- [ ] **Step 4: Register the models in `db.py` and `conftest.py`**

In `not_dot_net/backend/db.py`, inside `create_db_and_tables()`, add alongside the existing model imports:

```python
    import not_dot_net.backend.floorplan_models  # noqa: F401 — register FloorPlan/MapPoint with Base
```//

Insert it directly after the `import not_dot_net.backend.booking_models` line.

In `tests/conftest.py`, inside `setup_db`, add alongside the existing model imports (after `import not_dot_net.backend.booking_models`):

```python
    import not_dot_net.backend.floorplan_models  # noqa: F401
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_floorplan_models.py -v`
Expected: 3 passed

- [ ] **Step 6: Write the migration**

Check the current head first:

Run: `grep -l 'down_revision = "0016"' alembic/versions/*.py`
Expected: no output (0016 is head, confirmed earlier in this plan's research)

```python
# alembic/versions/0017_add_floorplan.py
"""Add floor_plan and map_point tables.

Revision ID: 0017
Revises: 0016
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "floor_plan",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("image_path", sa.String(length=500), nullable=False),
        sa.Column("width_px", sa.Integer(), nullable=False),
        sa.Column("height_px", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "map_point",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("floor_plan_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("x", sa.Integer(), nullable=False),
        sa.Column("y", sa.Integer(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["floor_plan_id"], ["floor_plan.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resource_id"], ["resource.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_map_point_floor_plan_id", "map_point", ["floor_plan_id"])


def downgrade() -> None:
    op.drop_index("ix_map_point_floor_plan_id", table_name="map_point")
    op.drop_table("map_point")
    op.drop_table("floor_plan")
```

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/backend/floorplan_models.py alembic/versions/0017_add_floorplan.py \
        not_dot_net/backend/db.py tests/conftest.py tests/test_floorplan_models.py
git commit -m "feat: add FloorPlan and MapPoint models + migration"
```

---

### Task 2: Backend service — floor plan CRUD (image upload, validate, resize, store)

**Files:**
- Create: `not_dot_net/backend/floorplan_service.py`
- Test: `tests/test_floorplan_service.py`

**Interfaces:**
- Consumes: `FloorPlan` from Task 1 (`not_dot_net.backend.floorplan_models`).
- Produces: `MANAGE_FLOORPLANS: str` (permission key), `process_floorplan_image(content: bytes) -> tuple[bytes, int, int] | None` (returns `(jpeg_bytes, width, height)` or `None` if invalid), `create_floor_plan(name: str, content: bytes, actor=None) -> FloorPlan`, `list_floor_plans(active_only: bool = True) -> list[FloorPlan]`, `get_floor_plan_image(floor_plan_id: uuid.UUID) -> bytes | None`, `delete_floor_plan(floor_plan_id: uuid.UUID, actor=None) -> None`. These are consumed by Task 4/5 (frontend) and Task 3 (map points, same module).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_floorplan_service.py
"""Tests for floorplan service — floor plan CRUD and image handling."""

import uuid

import pytest
from PIL import Image
from io import BytesIO

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.roles import RoleDefinition, roles_config


def _make_image_bytes(width=400, height=300, fmt="PNG") -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format=fmt)
    return buf.getvalue()


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(label="Admin", permissions=["manage_floorplans"])
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=[])
    await roles_config.set(cfg)


async def _create_user(email="user@test.com", role="staff") -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def test_process_floorplan_image_returns_dimensions():
    from not_dot_net.backend.floorplan_service import process_floorplan_image

    result = process_floorplan_image(_make_image_bytes(400, 300))
    assert result is not None
    jpeg_bytes, width, height = result
    assert width == 400
    assert height == 300
    assert jpeg_bytes[:2] == b"\xff\xd8"  # JPEG magic bytes


def test_process_floorplan_image_downsizes_oversized_plans():
    from not_dot_net.backend.floorplan_service import (
        FLOORPLAN_MAX_DIMENSION_PX, process_floorplan_image,
    )

    result = process_floorplan_image(_make_image_bytes(5000, 3000))
    assert result is not None
    _, width, height = result
    assert max(width, height) == FLOORPLAN_MAX_DIMENSION_PX


def test_process_floorplan_image_rejects_garbage():
    from not_dot_net.backend.floorplan_service import process_floorplan_image

    assert process_floorplan_image(b"not an image") is None


async def test_create_floor_plan_requires_manage_floorplans_permission():
    from not_dot_net.backend.floorplan_service import create_floor_plan

    await _setup_roles()
    staff = await _create_user(role="staff")
    with pytest.raises(PermissionError):
        await create_floor_plan("Test Plan", _make_image_bytes(), actor=staff)


async def test_create_and_list_floor_plans():
    from not_dot_net.backend.floorplan_service import create_floor_plan, list_floor_plans

    await _setup_roles()
    admin = await _create_user(role="admin")
    fp = await create_floor_plan("Building A - Floor 1", _make_image_bytes(400, 300), actor=admin)
    assert fp.width_px == 400
    assert fp.height_px == 300

    plans = await list_floor_plans()
    assert [p.name for p in plans] == ["Building A - Floor 1"]


async def test_create_floor_plan_rejects_invalid_image():
    from not_dot_net.backend.floorplan_service import create_floor_plan

    await _setup_roles()
    admin = await _create_user(role="admin")
    with pytest.raises(ValueError, match="Invalid image"):
        await create_floor_plan("Bad Plan", b"garbage", actor=admin)


async def test_get_floor_plan_image_round_trips_bytes():
    from not_dot_net.backend.floorplan_service import create_floor_plan, get_floor_plan_image

    await _setup_roles()
    admin = await _create_user(role="admin")
    fp = await create_floor_plan("Roundtrip", _make_image_bytes(200, 150), actor=admin)

    stored = await get_floor_plan_image(fp.id)
    assert stored is not None
    with Image.open(BytesIO(stored)) as img:
        assert img.size == (200, 150)


async def test_delete_floor_plan_requires_permission_and_removes_file():
    from not_dot_net.backend.floorplan_service import (
        create_floor_plan, delete_floor_plan, get_floor_plan_image, list_floor_plans,
    )

    await _setup_roles()
    admin = await _create_user(role="admin")
    staff = await _create_user(email="staff@test.com", role="staff")
    fp = await create_floor_plan("ToDelete", _make_image_bytes(), actor=admin)

    with pytest.raises(PermissionError):
        await delete_floor_plan(fp.id, actor=staff)

    await delete_floor_plan(fp.id, actor=admin)
    assert await list_floor_plans() == []
    assert await get_floor_plan_image(fp.id) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_floorplan_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'not_dot_net.backend.floorplan_service'`

- [ ] **Step 3: Write the service**

```python
# not_dot_net/backend/floorplan_service.py
"""Floor plan service — plan image CRUD and map point placement."""

import os
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError, ImageOps
from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.floorplan_models import FloorPlan
from not_dot_net.backend.permissions import check_permission, permission

MANAGE_FLOORPLANS = permission(
    "manage_floorplans", "Manage floor plans",
    "Upload floor plans and place map points",
)

FLOORPLAN_ROOT = Path(os.environ.get("NDN_DATA_DIR", "data")) / "floorplans"
FLOORPLAN_MAX_DIMENSION_PX = 2400
FLOORPLAN_JPEG_QUALITY = 90


def _rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        background = Image.new("RGB", image.size, "white")
        alpha = image.convert("RGBA").getchannel("A")
        background.paste(image.convert("RGBA"), mask=alpha)
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def process_floorplan_image(content: bytes) -> tuple[bytes, int, int] | None:
    """Validate + normalize an uploaded floor plan image.

    Re-encodes as JPEG, capped to FLOORPLAN_MAX_DIMENSION_PX on the long
    side, so map-point pixel coordinates and the base64 payload sent to the
    browser stay bounded regardless of what an admin uploads.
    """
    try:
        with Image.open(BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(
                (FLOORPLAN_MAX_DIMENSION_PX, FLOORPLAN_MAX_DIMENSION_PX),
                Image.Resampling.LANCZOS,
            )
            image = _rgb(image)
            output = BytesIO()
            image.save(output, format="JPEG", quality=FLOORPLAN_JPEG_QUALITY, optimize=True)
            return output.getvalue(), image.width, image.height
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError):
        return None


async def create_floor_plan(name: str, content: bytes, actor=None) -> FloorPlan:
    if actor is not None:
        await check_permission(actor, MANAGE_FLOORPLANS)
    processed = process_floorplan_image(content)
    if processed is None:
        raise ValueError("Invalid image")
    jpeg_bytes, width, height = processed

    floor_plan_id = uuid.uuid4()
    FLOORPLAN_ROOT.mkdir(parents=True, exist_ok=True)
    image_path = FLOORPLAN_ROOT / f"{floor_plan_id}.jpg"
    image_path.write_bytes(jpeg_bytes)

    async with session_scope() as session:
        fp = FloorPlan(
            id=floor_plan_id, name=name, image_path=str(image_path),
            width_px=width, height_px=height,
        )
        session.add(fp)
        await session.commit()
        await session.refresh(fp)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "floorplan", "create",
        actor_id=(actor.id if actor else None),
        target_type="floor_plan", target_id=fp.id,
        detail=f"name={name}",
    )
    return fp


async def list_floor_plans(active_only: bool = True) -> list[FloorPlan]:
    async with session_scope() as session:
        query = select(FloorPlan).order_by(FloorPlan.name)
        if active_only:
            query = query.where(FloorPlan.active == True)  # noqa: E712
        return list((await session.execute(query)).scalars().all())


async def get_floor_plan_image(floor_plan_id: uuid.UUID) -> bytes | None:
    async with session_scope() as session:
        fp = await session.get(FloorPlan, floor_plan_id)
        if fp is None:
            return None
    path = Path(fp.image_path)
    if not path.is_file():
        return None
    return path.read_bytes()


async def delete_floor_plan(floor_plan_id: uuid.UUID, actor=None) -> None:
    if actor is not None:
        await check_permission(actor, MANAGE_FLOORPLANS)
    async with session_scope() as session:
        fp = await session.get(FloorPlan, floor_plan_id)
        if fp is None:
            raise ValueError(f"Floor plan {floor_plan_id} not found")
        deleted_name = fp.name
        image_path = Path(fp.image_path)
        await session.delete(fp)
        await session.commit()
    image_path.unlink(missing_ok=True)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "floorplan", "delete",
        actor_id=(actor.id if actor else None),
        target_type="floor_plan", target_id=floor_plan_id,
        detail=f"name={deleted_name}",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_floorplan_service.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/floorplan_service.py tests/test_floorplan_service.py
git commit -m "feat: add floor plan CRUD service with image processing"
```

---

### Task 3: Backend service — map points (place, list, delete, nearest-point lookup)

**Files:**
- Modify: `not_dot_net/backend/floorplan_service.py`
- Test: `tests/test_floorplan_map_points.py`

**Interfaces:**
- Consumes: `MANAGE_FLOORPLANS`, `FLOORPLAN_ROOT` from Task 2 (same module). `MapPoint`/`FloorPlan` from Task 1.
- Produces: `add_map_point(floor_plan_id: uuid.UUID, label: str, kind: str, x: int, y: int, actor=None) -> MapPoint`, `list_map_points(floor_plan_id: uuid.UUID) -> list[MapPoint]`, `delete_map_point(map_point_id: uuid.UUID, actor=None) -> None`, `nearest_map_point(points: list[MapPoint], x: int, y: int, radius: int = 15) -> MapPoint | None` (pure function — no DB access, used by Task 4's click-to-identify-a-pin UI).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_floorplan_map_points.py
"""Tests for map point placement, listing, deletion, and hit-testing."""

import uuid

import pytest
from PIL import Image
from io import BytesIO

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.roles import RoleDefinition, roles_config


def _make_image_bytes(width=400, height=300) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(label="Admin", permissions=["manage_floorplans"])
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=[])
    await roles_config.set(cfg)


async def _create_user(email="user@test.com", role="staff") -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _create_floor_plan(actor):
    from not_dot_net.backend.floorplan_service import create_floor_plan
    return await create_floor_plan("Plan", _make_image_bytes(), actor=actor)


async def test_add_map_point_requires_permission():
    from not_dot_net.backend.floorplan_service import add_map_point

    await _setup_roles()
    admin = await _create_user(role="admin")
    staff = await _create_user(email="staff@test.com", role="staff")
    fp = await _create_floor_plan(admin)

    with pytest.raises(PermissionError):
        await add_map_point(fp.id, "Room 101", "room", 50, 60, actor=staff)


async def test_add_and_list_map_points():
    from not_dot_net.backend.floorplan_service import add_map_point, list_map_points

    await _setup_roles()
    admin = await _create_user(role="admin")
    fp = await _create_floor_plan(admin)

    await add_map_point(fp.id, "Room 101", "room", 50, 60, actor=admin)
    await add_map_point(fp.id, "Plug 12", "wall_plug", 120, 200, actor=admin)

    points = await list_map_points(fp.id)
    assert {p.label for p in points} == {"Room 101", "Plug 12"}


async def test_delete_map_point_requires_permission():
    from not_dot_net.backend.floorplan_service import add_map_point, delete_map_point

    await _setup_roles()
    admin = await _create_user(role="admin")
    staff = await _create_user(email="staff@test.com", role="staff")
    fp = await _create_floor_plan(admin)
    point = await add_map_point(fp.id, "Room 101", "room", 50, 60, actor=admin)

    with pytest.raises(PermissionError):
        await delete_map_point(point.id, actor=staff)

    await delete_map_point(point.id, actor=admin)
    assert await list_map_points(fp.id) == []


def test_nearest_map_point_finds_closest_within_radius():
    from not_dot_net.backend.floorplan_models import MapPoint
    from not_dot_net.backend.floorplan_service import nearest_map_point

    near = MapPoint(floor_plan_id=uuid.uuid4(), label="Near", kind="room", x=100, y=100)
    far = MapPoint(floor_plan_id=uuid.uuid4(), label="Far", kind="room", x=500, y=500)

    assert nearest_map_point([near, far], 105, 102) is near


def test_nearest_map_point_returns_none_outside_radius():
    from not_dot_net.backend.floorplan_models import MapPoint
    from not_dot_net.backend.floorplan_service import nearest_map_point

    point = MapPoint(floor_plan_id=uuid.uuid4(), label="Room", kind="room", x=100, y=100)

    assert nearest_map_point([point], 200, 200) is None


def test_nearest_map_point_handles_empty_list():
    from not_dot_net.backend.floorplan_service import nearest_map_point

    assert nearest_map_point([], 0, 0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_floorplan_map_points.py -v`
Expected: FAIL with `ImportError: cannot import name 'add_map_point' from 'not_dot_net.backend.floorplan_service'`

- [ ] **Step 3: Add map point functions to the service**

Append to `not_dot_net/backend/floorplan_service.py` (after `delete_floor_plan`):

```python
from not_dot_net.backend.floorplan_models import MapPoint  # add to the existing import block above


async def add_map_point(
    floor_plan_id: uuid.UUID, label: str, kind: str, x: int, y: int, actor=None,
) -> MapPoint:
    if actor is not None:
        await check_permission(actor, MANAGE_FLOORPLANS)
    async with session_scope() as session:
        point = MapPoint(floor_plan_id=floor_plan_id, label=label, kind=kind, x=x, y=y)
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


async def list_map_points(floor_plan_id: uuid.UUID) -> list[MapPoint]:
    async with session_scope() as session:
        query = select(MapPoint).where(MapPoint.floor_plan_id == floor_plan_id)
        return list((await session.execute(query)).scalars().all())


async def delete_map_point(map_point_id: uuid.UUID, actor=None) -> None:
    if actor is not None:
        await check_permission(actor, MANAGE_FLOORPLANS)
    async with session_scope() as session:
        point = await session.get(MapPoint, map_point_id)
        if point is None:
            raise ValueError(f"Map point {map_point_id} not found")
        floor_plan_id, label = point.floor_plan_id, point.label
        await session.delete(point)
        await session.commit()

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "floorplan", "delete_point",
        actor_id=(actor.id if actor else None),
        target_type="floor_plan", target_id=floor_plan_id,
        detail=f"label={label}",
    )


def nearest_map_point(points: list[MapPoint], x: int, y: int, radius: int = 15) -> MapPoint | None:
    """Closest point to (x, y) within radius pixels, or None. Pure/no DB —
    used by the frontend to turn an image click into 'which pin was that'."""
    best: MapPoint | None = None
    best_dist_sq = radius * radius
    for point in points:
        dist_sq = (point.x - x) ** 2 + (point.y - y) ** 2
        if dist_sq <= best_dist_sq:
            best, best_dist_sq = point, dist_sq
    return best
```

Move the `from not_dot_net.backend.floorplan_models import FloorPlan` import at the top of the file to `from not_dot_net.backend.floorplan_models import FloorPlan, MapPoint` instead of re-importing inline — clean up rather than leaving two import lines for the same module.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_floorplan_map_points.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/floorplan_service.py tests/test_floorplan_map_points.py
git commit -m "feat: add map point placement, listing, deletion, and hit-testing"
```

---

### Task 4: Frontend — read-only floor plan view

**Files:**
- Create: `not_dot_net/frontend/floorplan.py`
- Modify: `not_dot_net/frontend/i18n.py`
- Test: `tests/test_floorplan_ui_helpers.py`

**Interfaces:**
- Consumes: `list_floor_plans`, `get_floor_plan_image`, `list_map_points`, `nearest_map_point` from `not_dot_net.backend.floorplan_service`.
- Produces: `render(user) -> Callable[[], Awaitable[None]]` (same shape as `bookings.py::render`, consumed by Task 6's `shell.py` wiring). Pure helpers `_floorplan_image_data_uri(content: bytes) -> str` and `_points_svg(points, highlight_id=None) -> str`, tested directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_floorplan_ui_helpers.py
import uuid

from not_dot_net.backend.floorplan_models import MapPoint


def test_floorplan_image_data_uri_wraps_jpeg_bytes():
    from not_dot_net.frontend.floorplan import _floorplan_image_data_uri

    uri = _floorplan_image_data_uri(b"\xff\xd8\xff\xe0fake")
    assert uri.startswith("data:image/jpeg;base64,")


def test_points_svg_contains_circle_per_point():
    from not_dot_net.frontend.floorplan import _points_svg

    points = [
        MapPoint(floor_plan_id=uuid.uuid4(), label="Room 101", kind="room", x=50, y=60),
        MapPoint(floor_plan_id=uuid.uuid4(), label="Plug 12", kind="wall_plug", x=120, y=200),
    ]
    svg = _points_svg(points)
    assert svg.count("<circle") == 2
    assert 'cx="50" cy="60"' in svg
    assert 'cx="120" cy="200"' in svg


def test_points_svg_escapes_label_special_characters():
    from not_dot_net.frontend.floorplan import _points_svg

    points = [MapPoint(floor_plan_id=uuid.uuid4(), label="A&B <test>", kind="room", x=10, y=10)]
    svg = _points_svg(points)
    assert "A&B <test>" not in svg
    assert "&amp;" in svg
    assert "&lt;test&gt;" in svg


def test_points_svg_highlights_matching_point():
    from not_dot_net.frontend.floorplan import _points_svg

    target = MapPoint(floor_plan_id=uuid.uuid4(), label="Room 101", kind="room", x=50, y=60)
    other = MapPoint(floor_plan_id=uuid.uuid4(), label="Room 102", kind="room", x=90, y=60)
    svg = _points_svg([target, other], highlight_id=target.id)
    assert svg.count('stroke="black"') == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'not_dot_net.frontend.floorplan'`

- [ ] **Step 3: Add i18n keys**

In `not_dot_net/frontend/i18n.py`, add to the `"en"` dict (near the `"bookings"` block, e.g. right after `"resource_restored": "Resource restored",`):

```python
        "floorplan": "Floor Plan",
        "floorplan_select": "Floor plan",
        "floorplan_none": "No floor plans yet.",
        "floorplan_add": "Add floor plan",
        "floorplan_name": "Name",
        "floorplan_upload_image": "Plan image",
        "floorplan_uploaded": "Floor plan added",
        "floorplan_upload_failed": "Could not process that image",
        "floorplan_deleted": "Floor plan deleted",
        "floorplan_delete_confirm": "Delete this floor plan and all its pins?",
        "floorplan_place_pin_mode": "Place pin",
        "floorplan_pin_label": "Label",
        "floorplan_pin_kind": "Kind",
        "floorplan_pin_added": "Pin added",
        "floorplan_pin_deleted": "Pin deleted",
        "floorplan_pin_delete_confirm": "Delete this pin?",
        "kind_room": "Room",
        "kind_desk": "Desk",
        "kind_wall_plug": "Wall plug",
        "kind_asset": "Asset",
        "kind_other": "Other",
```

Add the matching `"fr"` block at the same relative position in the `"fr"` dict (near `"resource_restored": "Ressource restaurée",`):

```python
        "floorplan": "Plan des locaux",
        "floorplan_select": "Plan",
        "floorplan_none": "Aucun plan pour le moment.",
        "floorplan_add": "Ajouter un plan",
        "floorplan_name": "Nom",
        "floorplan_upload_image": "Image du plan",
        "floorplan_uploaded": "Plan ajouté",
        "floorplan_upload_failed": "Impossible de traiter cette image",
        "floorplan_deleted": "Plan supprimé",
        "floorplan_delete_confirm": "Supprimer ce plan et tous ses points ?",
        "floorplan_place_pin_mode": "Placer un point",
        "floorplan_pin_label": "Étiquette",
        "floorplan_pin_kind": "Type",
        "floorplan_pin_added": "Point ajouté",
        "floorplan_pin_deleted": "Point supprimé",
        "floorplan_pin_delete_confirm": "Supprimer ce point ?",
        "kind_room": "Salle",
        "kind_desk": "Bureau",
        "kind_wall_plug": "Prise réseau",
        "kind_asset": "Équipement",
        "kind_other": "Autre",
```

- [ ] **Step 4: Write the page**

```python
# not_dot_net/frontend/floorplan.py
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/frontend/floorplan.py not_dot_net/frontend/i18n.py tests/test_floorplan_ui_helpers.py
git commit -m "feat: add read-only floor plan view page"
```

---

### Task 5: Frontend — admin controls (upload plan, place/delete pins, delete plan)

**Files:**
- Modify: `not_dot_net/frontend/floorplan.py`
- Test: `tests/test_floorplan_ui_helpers.py` (add cases)

**Interfaces:**
- Consumes: `create_floor_plan`, `delete_floor_plan`, `add_map_point`, `delete_map_point` from `not_dot_net.backend.floorplan_service` (Tasks 2/3). `MANAGE_FLOORPLANS` role gating already established in Task 4.
- Produces: nothing new consumed by later tasks — this is the last page-internal task before shell wiring.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_floorplan_ui_helpers.py
def test_pin_kind_options_cover_all_kind_colors():
    """The kind dropdown offered in the add-pin dialog must stay in sync with
    the colors _points_svg knows how to render — a kind with no color entry
    silently renders grey, which would be confusing in the picker."""
    from not_dot_net.frontend.floorplan import _KIND_COLOR, PIN_KINDS

    assert set(PIN_KINDS) == set(_KIND_COLOR)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py::test_pin_kind_options_cover_all_kind_colors -v`
Expected: FAIL with `ImportError: cannot import name 'PIN_KINDS'`

- [ ] **Step 3: Add admin controls to the page**

In `not_dot_net/frontend/floorplan.py`, add the import and constant near `_KIND_COLOR`:

```python
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

# Plain keys only — do NOT resolve translations at module import time. `t()`
# reads `app.storage.user` via `get_locale()`, which requires an active
# NiceGUI page/client context; calling it at import time raises. Build the
# translated {key: label} dict inside a render function instead (see
# `_pin_kind_select_options` below).
PIN_KINDS = ["room", "desk", "wall_plug", "asset", "other"]


def _pin_kind_select_options() -> dict[str, str]:
    return {kind: t(f"kind_{kind}") for kind in PIN_KINDS}
```

Add an "Add floor plan" button to the empty-state branch in `_render_floorplan` (Task 4 left this branch as just a label with no admin affordance — add the button now):

```python
        if not plans:
            ui.label(t("floorplan_none")).classes("text-grey")
            if is_admin:
                ui.button(
                    t("floorplan_add"), icon="add",
                    on_click=lambda: _show_add_plan_dialog(container, user),
                ).props("color=primary")
            return
```

Add an "Add floor plan" button next to the select in `_render_floorplan` (right after the `if len(plans) > 1:` select block, still inside `with container:`):

```python
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
```

Rework `_render_plan_area` to support place-pin mode and pin deletion:

```python
async def _render_plan_area(plan_area, state, user, is_admin):
    plan_area.clear()
    plan = state["selected"]
    image_bytes = await get_floor_plan_image(plan.id)
    points = await list_map_points(plan.id)
    place_mode = {"on": False}

    with plan_area:
        if image_bytes is None:
            ui.label(t("floorplan_none")).classes("text-grey")
            return

        if is_admin:
            ui.switch(t("floorplan_place_pin_mode"), value=False,
                      on_change=lambda e: place_mode.__setitem__("on", e.value))

        image = ui.interactive_image(
            source=_floorplan_image_data_uri(image_bytes),
            content=_points_svg(points, state["highlight_id"]),
        ).classes("w-full border rounded")

        async def on_mouse(e):
            x, y = round(e.image_x), round(e.image_y)
            if is_admin and place_mode["on"]:
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
    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label(t("floorplan_pin_label")).classes("text-subtitle2")
        label_input = ui.input(t("floorplan_pin_label")).props("outlined dense").classes("w-full")
        kind_select = ui.select(
            _pin_kind_select_options(), value="room", label=t("floorplan_pin_kind"),
        ).props("outlined dense").classes("w-full")

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            async def do_save():
                if not label_input.value.strip():
                    ui.notify(t("required_field"), color="negative")
                    return
                await add_map_point(
                    floor_plan_id, label_input.value.strip(), kind_select.value, x, y, actor=user,
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
```

Note: `_render_floorplan` must pass `is_admin` through to the initial `_render_plan_area` call — update its trailing call:

```python
        await _render_plan_area(plan_area, state, user, is_admin)
```

(This line already exists at the end of `_render_floorplan` from Task 4 — no change needed there, just confirming the signature now matches.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/floorplan.py tests/test_floorplan_ui_helpers.py
git commit -m "feat: add admin controls for floor plan upload and pin placement"
```

---

### Task 6: Wire into the shell tab, register model everywhere, full-suite verification

**Files:**
- Modify: `not_dot_net/frontend/shell.py`

**Interfaces:**
- Consumes: `render` from `not_dot_net.frontend.floorplan` (Task 4/5).

- [ ] **Step 1: Add the import**

In `not_dot_net/frontend/shell.py`, add after the `render_bookings` import:

```python
from not_dot_net.frontend.floorplan import render as render_floorplan
```

- [ ] **Step 2: Add the tab label, key, and tab**

Add after `bookings_label = t("bookings")`:

```python
        floorplan_label = t("floorplan")
```

Add `floorplan_label` right after `bookings_label` in `available_tabs`:

```python
        available_tabs = [dashboard_label, people_label, bookings_label, floorplan_label, pages_label]
```

Add `"floorplan": floorplan_label,` to `tab_keys` (after `"bookings": bookings_label,`):

```python
        tab_keys = {
            "dashboard": dashboard_label,
            "people": people_label,
            "bookings": bookings_label,
            "floorplan": floorplan_label,
            "pages": pages_label,
            ...
        }
```

Add the `ui.tab` call right after `ui.tab(bookings_label, icon="event_available")`:

```python
                ui.tab(floorplan_label, icon="map")
```

- [ ] **Step 3: Add the tab panel**

Add right after the `bookings_label` tab panel:

```python
            with ui.tab_panel(floorplan_label):
                refreshers[floorplan_label] = render_floorplan(effective_user)
```

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest -q`
Expected: all tests pass (previous count + the ~19 new tests added across Tasks 1–5); read the actual pass count and exit code, don't infer from a partial grep.

- [ ] **Step 5: Manual smoke check**

Run: `uv run python -m not_dot_net.cli serve --host localhost --port 8088`

Log in as the dev auto-admin, open the new "Floor Plan" tab, upload a small JPEG/PNG, toggle "Place pin", click the image to add a couple of pins of different kinds, reload the page and confirm the pins persist and render, click an existing pin to see the label/delete dialog, delete a pin, then delete the floor plan and confirm the empty state returns.

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/frontend/shell.py
git commit -m "feat: wire floor plan tab into the shell"
```

---

## Out of scope for this plan (follow-up phases, per the earlier research)

- Linking a `MapPoint` to an existing `Resource` (booking-click-to-book) — `MapPoint.resource_id` column already exists in the schema so this is additive, no migration needed later.
- Wall-plug network metadata (patch-panel port / switch / VLAN) — planned as `Resource(resource_type="wall_plug")` with `specs` JSON, reusing the booking system's existing `Resource` model; not needed until a `MapPoint` actually needs to point at one.
- Drag-to-reposition existing pins, multi-floor navigation UI polish, exporting/printing a plan with pins.
