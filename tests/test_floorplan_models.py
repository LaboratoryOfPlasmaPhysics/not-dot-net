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
