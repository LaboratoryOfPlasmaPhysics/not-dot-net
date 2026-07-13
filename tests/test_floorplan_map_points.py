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


async def _create_floor_plan(actor, monkeypatch, tmp_path):
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    return await fs.create_floor_plan("Plan", _make_image_bytes(), actor=actor)


async def test_add_map_point_requires_permission(monkeypatch, tmp_path):
    from not_dot_net.backend.floorplan_service import add_map_point

    await _setup_roles()
    admin = await _create_user(role="admin")
    staff = await _create_user(email="staff@test.com", role="staff")
    fp = await _create_floor_plan(admin, monkeypatch, tmp_path)

    with pytest.raises(PermissionError):
        await add_map_point(fp.id, "Room 101", "room", 50, 60, actor=staff)


async def test_add_and_list_map_points(monkeypatch, tmp_path):
    from not_dot_net.backend.floorplan_service import add_map_point, list_map_points

    await _setup_roles()
    admin = await _create_user(role="admin")
    fp = await _create_floor_plan(admin, monkeypatch, tmp_path)

    await add_map_point(fp.id, "Room 101", "room", 50, 60, actor=admin)
    await add_map_point(fp.id, "Plug 12", "wall_plug", 120, 200, actor=admin)

    points = await list_map_points(fp.id)
    assert {p.label for p in points} == {"Room 101", "Plug 12"}


async def test_delete_map_point_requires_permission(monkeypatch, tmp_path):
    from not_dot_net.backend.floorplan_service import add_map_point, delete_map_point, list_map_points

    await _setup_roles()
    admin = await _create_user(role="admin")
    staff = await _create_user(email="staff@test.com", role="staff")
    fp = await _create_floor_plan(admin, monkeypatch, tmp_path)
    point = await add_map_point(fp.id, "Room 101", "room", 50, 60, actor=admin)

    with pytest.raises(PermissionError):
        await delete_map_point(point.id, actor=staff)

    await delete_map_point(point.id, actor=admin)
    assert await list_map_points(fp.id) == []


async def test_add_map_point_with_resource_id_links_to_resource(monkeypatch, tmp_path):
    from not_dot_net.backend.booking_service import create_resource
    from not_dot_net.backend.floorplan_service import add_map_point

    await _setup_roles()
    admin = await _create_user(role="admin")
    fp = await _create_floor_plan(admin, monkeypatch, tmp_path)
    resource = await create_resource("Room 101", "office", location="Palaiseau")

    point = await add_map_point(fp.id, "Room 101", "room", 50, 60, resource_id=resource.id, actor=admin)
    assert point.resource_id == resource.id


async def test_add_map_point_without_resource_id_defaults_none(monkeypatch, tmp_path):
    from not_dot_net.backend.floorplan_service import add_map_point

    await _setup_roles()
    admin = await _create_user(role="admin")
    fp = await _create_floor_plan(admin, monkeypatch, tmp_path)

    point = await add_map_point(fp.id, "Plug 1", "wall_plug", 5, 5, actor=admin)
    assert point.resource_id is None


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
