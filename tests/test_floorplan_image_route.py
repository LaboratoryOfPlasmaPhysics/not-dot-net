"""P8 — the floor-plan image must be a cacheable URL, not a base64 prop.

The image was re-read from disk, base64-encoded and shipped over the websocket
on every plan-area re-render: pin add/delete, zone edit, availability
offer/revoke and every office booking. At 0.7-2.7 MB per render that dwarfs
everything else on the page.
"""
import uuid
from io import BytesIO

import pytest
from PIL import Image

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.floorplan_service import create_floor_plan


def _image_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (120, 80), "white").save(buf, format="JPEG")
    return buf.getvalue()


async def _make_admin() -> User:
    async with session_scope() as session:
        user = User(
            email=f"fp-admin-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x", is_active=True, is_superuser=True, role="",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def test_image_url_points_at_the_route():
    from not_dot_net.frontend.floorplan import _floorplan_image_url

    plan_id = uuid.uuid4()
    assert _floorplan_image_url(plan_id) == f"/floorplan/image/{plan_id}"


async def test_route_serves_the_image_bytes(user, monkeypatch, tmp_path):
    from not_dot_net.backend import floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()
    plan = await create_floor_plan("Route Plan", _image_bytes(), actor=admin)

    response = await user.http_client.get(f"/floorplan/image/{plan.id}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"  # JPEG SOI


async def test_route_sets_a_cache_header(user, monkeypatch, tmp_path):
    from not_dot_net.backend import floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()
    plan = await create_floor_plan("Cached Plan", _image_bytes(), actor=admin)

    response = await user.http_client.get(f"/floorplan/image/{plan.id}")
    assert "cache-control" in response.headers
    assert "max-age" in response.headers["cache-control"]


async def test_unknown_plan_is_404(user):
    response = await user.http_client.get(f"/floorplan/image/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_malformed_id_is_404(user):
    response = await user.http_client.get("/floorplan/image/not-a-uuid")
    assert response.status_code == 404


async def test_image_exists_check_does_not_read_the_file(monkeypatch, tmp_path):
    """The re-render path only needs to know whether an image is there.

    Reading the whole file back just to compare against None re-read
    0.7-2.7 MB from disk on every pin add, zone edit and office booking.
    """
    from not_dot_net.backend import floorplan_service as fs
    from not_dot_net.backend.floorplan_service import floor_plan_image_exists

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()
    plan = await create_floor_plan("Exists Plan", _image_bytes(), actor=admin)

    def boom(*a, **k):
        raise AssertionError("read the image bytes for an existence check")

    monkeypatch.setattr(fs.Path, "read_bytes", boom)
    assert await floor_plan_image_exists(plan.id) is True
    assert await floor_plan_image_exists(uuid.uuid4()) is False


async def test_image_exists_is_false_when_the_file_is_gone(monkeypatch, tmp_path):
    from not_dot_net.backend import floorplan_service as fs
    from not_dot_net.backend.floorplan_service import floor_plan_image_exists

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()
    plan = await create_floor_plan("Orphan Plan", _image_bytes(), actor=admin)

    (tmp_path / f"{plan.id}.jpg").unlink()
    assert await floor_plan_image_exists(plan.id) is False


async def test_failed_create_does_not_leave_an_orphan_image(monkeypatch, tmp_path):
    """I12 — the JPEG is written before the DB commit, and only IntegrityError
    cleaned it up. Any other failure left an orphan file with nothing pointing
    at it and no reaper to collect it."""
    from not_dot_net.backend import floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()

    class _Boom(Exception):
        pass

    real_scope = fs.session_scope

    def exploding_scope(*a, **k):
        raise _Boom("database unavailable")

    monkeypatch.setattr(fs, "session_scope", exploding_scope)
    with pytest.raises(_Boom):
        await fs.create_floor_plan("Doomed Plan", _image_bytes(), actor=admin)

    monkeypatch.setattr(fs, "session_scope", real_scope)
    leftovers = list(tmp_path.glob("*.jpg"))
    assert leftovers == [], f"orphan image left behind: {leftovers}"
