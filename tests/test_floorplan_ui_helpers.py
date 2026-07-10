import uuid
from io import BytesIO

from nicegui import ui
from nicegui.testing import User
from PIL import Image

from not_dot_net.backend.db import User as DbUser, session_scope
from not_dot_net.backend.floorplan_models import MapPoint
from not_dot_net.backend.floorplan_service import create_floor_plan


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


def test_pin_kind_options_cover_all_kind_colors():
    """The kind dropdown offered in the add-pin dialog must stay in sync with
    the colors _points_svg knows how to render — a kind with no color entry
    silently renders grey, which would be confusing in the picker."""
    from not_dot_net.frontend.floorplan import _KIND_COLOR, PIN_KINDS

    assert set(PIN_KINDS) == set(_KIND_COLOR)


def _make_image_bytes(width=400, height=300) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


async def _make_admin(email="fp-admin@test.com") -> DbUser:
    async with session_scope() as session:
        db_user = DbUser(id=uuid.uuid4(), email=email, hashed_password="x", is_superuser=True)
        session.add(db_user)
        await session.commit()
        await session.refresh(db_user)
        return db_user


async def test_place_pin_mode_persists_across_pin_area_rerender(
    user: User, monkeypatch, tmp_path
) -> None:
    """Reproducer: _render_plan_area used to hardcode the "Place pin" switch
    back to off every time it re-rendered (e.g. right after a pin was added),
    forcing an admin placing several pins in a row to re-toggle it before
    every click. The switch's initial value must come from persisted state."""
    from not_dot_net.frontend.floorplan import _render_plan_area
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)

    admin = await _make_admin()
    plan = await create_floor_plan("Reproducer Plan", _make_image_bytes(), actor=admin)

    @ui.page("/floorplan-rerender-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": False}
        await _render_plan_area(area, state, admin, True)

        async def simulate_pin_added():
            # Mirrors what _show_add_pin_dialog's do_save does: the switch
            # was already toggled on by the admin, then a pin gets added and
            # the plan area re-renders.
            state["place_mode"] = True
            await _render_plan_area(area, state, admin, True)
            with area:
                ui.label("rerender-complete")

        ui.button("simulate-pin-added", on_click=simulate_pin_added)

    await user.open("/floorplan-rerender-test")
    user.find("simulate-pin-added").click()
    await user.should_see("rerender-complete")

    switches = list(user.find(kind=ui.switch).elements)
    assert len(switches) == 1
    assert switches[0].value is True
