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


async def test_create_floor_plan_requires_manage_floorplans_permission(tmp_path, monkeypatch):
    import not_dot_net.backend.floorplan_service as fs
    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)

    await _setup_roles()
    staff = await _create_user(role="staff")
    with pytest.raises(PermissionError):
        await fs.create_floor_plan("Test Plan", _make_image_bytes(), actor=staff)


async def test_create_and_list_floor_plans(tmp_path, monkeypatch):
    import not_dot_net.backend.floorplan_service as fs
    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    from not_dot_net.backend.floorplan_service import create_floor_plan, list_floor_plans

    await _setup_roles()
    admin = await _create_user(role="admin")
    fp = await create_floor_plan("Building A - Floor 1", _make_image_bytes(400, 300), actor=admin)
    assert fp.width_px == 400
    assert fp.height_px == 300

    plans = await list_floor_plans()
    assert [p.name for p in plans] == ["Building A - Floor 1"]


async def test_create_floor_plan_rejects_invalid_image(tmp_path, monkeypatch):
    import not_dot_net.backend.floorplan_service as fs
    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    from not_dot_net.backend.floorplan_service import create_floor_plan

    await _setup_roles()
    admin = await _create_user(role="admin")
    with pytest.raises(ValueError, match="Invalid image"):
        await create_floor_plan("Bad Plan", b"garbage", actor=admin)


async def test_get_floor_plan_image_round_trips_bytes(tmp_path, monkeypatch):
    import not_dot_net.backend.floorplan_service as fs
    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    from not_dot_net.backend.floorplan_service import create_floor_plan, get_floor_plan_image

    await _setup_roles()
    admin = await _create_user(role="admin")
    fp = await create_floor_plan("Roundtrip", _make_image_bytes(200, 150), actor=admin)

    stored = await get_floor_plan_image(fp.id)
    assert stored is not None
    with Image.open(BytesIO(stored)) as img:
        assert img.size == (200, 150)


async def test_create_floor_plan_duplicate_name_cleans_up_file(tmp_path, monkeypatch):
    import not_dot_net.backend.floorplan_service as fs
    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)

    await _setup_roles()
    admin = await _create_user(role="admin")
    await fs.create_floor_plan("Dup", _make_image_bytes(), actor=admin)

    with pytest.raises(ValueError, match="already exists"):
        await fs.create_floor_plan("Dup", _make_image_bytes(), actor=admin)

    plans = await fs.list_floor_plans()
    assert len(plans) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{plans[0].id}.jpg"]


async def test_delete_floor_plan_requires_permission_and_removes_file(tmp_path, monkeypatch):
    import not_dot_net.backend.floorplan_service as fs
    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
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
