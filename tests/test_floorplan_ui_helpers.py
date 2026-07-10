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


from datetime import date, timedelta


def test_clamp_range_to_window_keeps_value_inside_bounds():
    from not_dot_net.frontend.floorplan import _clamp_range_to_window

    result = _clamp_range_to_window(
        {"from": "2026-08-05", "to": "2026-08-08"},
        date(2026, 8, 1), date(2026, 8, 15),
    )
    assert result == {"from": "2026-08-05", "to": "2026-08-08"}


def test_clamp_range_to_window_clamps_start_before_window():
    from not_dot_net.frontend.floorplan import _clamp_range_to_window

    result = _clamp_range_to_window(
        {"from": "2026-07-20", "to": "2026-08-08"},
        date(2026, 8, 1), date(2026, 8, 15),
    )
    assert result == {"from": "2026-08-01", "to": "2026-08-08"}


def test_clamp_range_to_window_clamps_end_after_window():
    from not_dot_net.frontend.floorplan import _clamp_range_to_window

    result = _clamp_range_to_window(
        {"from": "2026-08-05", "to": "2026-09-01"},
        date(2026, 8, 1), date(2026, 8, 15),
    )
    assert result == {"from": "2026-08-05", "to": "2026-08-14"}


def test_clamp_range_to_window_falls_back_on_invalid_value():
    from not_dot_net.frontend.floorplan import _clamp_range_to_window

    result = _clamp_range_to_window(None, date(2026, 8, 1), date(2026, 8, 15))
    assert result == {"from": "2026-08-01", "to": "2026-08-14"}


def test_earliest_office_book_start_lead_time_wins_when_window_starts_today():
    from not_dot_net.frontend.floorplan import _earliest_office_book_start

    today = date(2026, 7, 10)
    result = _earliest_office_book_start(today, today, minimum_lead_days=7)
    assert result == date(2026, 7, 17)


def test_earliest_office_book_start_window_start_wins_when_far_in_future():
    from not_dot_net.frontend.floorplan import _earliest_office_book_start

    today = date(2026, 7, 10)
    window_start = date(2026, 8, 10)
    result = _earliest_office_book_start(window_start, today, minimum_lead_days=7)
    assert result == window_start


def test_earliest_office_book_start_boundary_when_equal():
    from not_dot_net.frontend.floorplan import _earliest_office_book_start

    today = date(2026, 7, 10)
    window_start = today + timedelta(days=7)
    result = _earliest_office_book_start(window_start, today, minimum_lead_days=7)
    assert result == window_start


async def _create_staff_user(email: str) -> DbUser:
    async with session_scope() as session:
        db_user = DbUser(id=uuid.uuid4(), email=email, hashed_password="x", is_active=True)
        session.add(db_user)
        await session.commit()
        await session.refresh(db_user)
        return db_user


async def test_pin_actions_shows_offer_button_for_owner(user: User, monkeypatch, tmp_path) -> None:
    from not_dot_net.backend.booking_service import create_resource
    from not_dot_net.backend.floorplan_service import add_map_point
    from not_dot_net.frontend.floorplan import _show_pin_actions
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)

    admin = await _make_admin()
    owner = await _create_staff_user(email="owner@test.com")
    resource = await create_resource("Room 301", "office", location="Palaiseau",
                                     owner_user_id=owner.id, actor=admin)
    plan = await create_floor_plan("Office Plan", _make_image_bytes(), actor=admin)
    point = await add_map_point(plan.id, "Room 301", "room", 50, 50,
                                resource_id=resource.id, actor=admin)

    @ui.page("/pin-actions-owner-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": False}
        await _show_pin_actions(area, state, owner, False, point)

    await user.open("/pin-actions-owner-test")
    await user.should_see(t("floorplan_offer_availability"))


async def test_pin_actions_hides_offer_button_for_stranger(user: User, monkeypatch, tmp_path) -> None:
    from not_dot_net.backend.booking_service import create_resource
    from not_dot_net.backend.floorplan_service import add_map_point
    from not_dot_net.frontend.floorplan import _show_pin_actions
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs
    import pytest

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)

    admin = await _make_admin()
    owner = await _create_staff_user(email="owner2@test.com")
    stranger = await _create_staff_user(email="stranger@test.com")
    resource = await create_resource("Room 302", "office", location="Palaiseau",
                                     owner_user_id=owner.id, actor=admin)
    plan = await create_floor_plan("Office Plan 2", _make_image_bytes(), actor=admin)
    point = await add_map_point(plan.id, "Room 302", "room", 50, 50,
                                resource_id=resource.id, actor=admin)

    @ui.page("/pin-actions-stranger-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": False}
        await _show_pin_actions(area, state, stranger, False, point)

    await user.open("/pin-actions-stranger-test")
    with pytest.raises(AssertionError):
        await user.should_see(t("floorplan_offer_availability"))


async def test_pin_actions_hides_offer_button_for_floorplan_admin_without_booking_permission(
    user: User, monkeypatch, tmp_path
) -> None:
    """Reproducer: can_offer used to be derived from `is_admin`, which is
    threaded from the `manage_floorplans` permission — a different
    permission than the one `offer_availability`/`revoke_availability`
    actually enforce (`manage_bookings`). A non-owner user who holds
    manage_floorplans but not manage_bookings must not see the Offer
    availability button, since the backend would reject the action."""
    from not_dot_net.backend.booking_service import create_resource
    from not_dot_net.backend.floorplan_service import add_map_point
    from not_dot_net.backend.roles import RoleDefinition, roles_config
    from not_dot_net.frontend.floorplan import _show_pin_actions
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs
    import pytest

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)

    cfg = await roles_config.get()
    cfg.roles["floorplan_manager"] = RoleDefinition(
        label="Floorplan Manager", permissions=["manage_floorplans"],
    )
    await roles_config.set(cfg)

    admin = await _make_admin()
    owner = await _create_staff_user(email="owner3@test.com")
    async with session_scope() as session:
        floorplan_manager = DbUser(
            id=uuid.uuid4(), email="fp-manager@test.com", hashed_password="x",
            is_active=True, role="floorplan_manager",
        )
        session.add(floorplan_manager)
        await session.commit()
        await session.refresh(floorplan_manager)

    resource = await create_resource("Room 303", "office", location="Palaiseau",
                                     owner_user_id=owner.id, actor=admin)
    plan = await create_floor_plan("Office Plan 3", _make_image_bytes(), actor=admin)
    point = await add_map_point(plan.id, "Room 303", "room", 50, 50,
                                resource_id=resource.id, actor=admin)

    @ui.page("/pin-actions-floorplan-admin-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": False}
        # is_admin=True mirrors what _render_floorplan computes from
        # manage_floorplans — the caller correctly grants floor-plan admin
        # UI (e.g. delete-pin), but that must NOT leak into the booking
        # permission gate.
        await _show_pin_actions(area, state, floorplan_manager, True, point)

    await user.open("/pin-actions-floorplan-admin-test")
    with pytest.raises(AssertionError):
        await user.should_see(t("floorplan_offer_availability"))


def test_resource_picker_visible_only_for_room_kind():
    from not_dot_net.frontend.floorplan import _resource_picker_visible

    assert _resource_picker_visible("room") is True
    assert _resource_picker_visible("desk") is False
    assert _resource_picker_visible("wall_plug") is False
    assert _resource_picker_visible("asset") is False
    assert _resource_picker_visible("other") is False


async def test_pin_actions_shows_edit_button_for_manage_bookings_admin(
    user: User, monkeypatch, tmp_path
) -> None:
    """An admin who can manage bookings must be able to reopen the resource
    dialog from the pin popup — office resources are excluded from the
    Bookings tab grid, so this popup is the only other way to edit them."""
    from not_dot_net.backend.booking_service import create_resource
    from not_dot_net.backend.floorplan_service import add_map_point
    from not_dot_net.frontend.floorplan import _show_pin_actions
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)

    admin = await _make_admin()
    owner = await _create_staff_user(email="owner4@test.com")
    resource = await create_resource("Room 401", "office", location="Palaiseau",
                                     owner_user_id=owner.id, actor=admin)
    plan = await create_floor_plan("Office Plan 4", _make_image_bytes(), actor=admin)
    point = await add_map_point(plan.id, "Room 401", "room", 50, 50,
                                resource_id=resource.id, actor=admin)

    @ui.page("/pin-actions-edit-admin-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": False}
        await _show_pin_actions(area, state, admin, True, point)

    await user.open("/pin-actions-edit-admin-test")
    await user.should_see(t("edit_resource"))


async def test_pin_actions_hides_edit_button_for_owner_non_admin(
    user: User, monkeypatch, tmp_path
) -> None:
    """The office's owner can offer/book/revoke availability, but editing the
    underlying Resource row (type, location, specs, owner reassignment) is an
    admin-only action distinct from that — the owner must not see Edit."""
    from not_dot_net.backend.booking_service import create_resource
    from not_dot_net.backend.floorplan_service import add_map_point
    from not_dot_net.frontend.floorplan import _show_pin_actions
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs
    import pytest

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)

    admin = await _make_admin()
    owner = await _create_staff_user(email="owner5@test.com")
    resource = await create_resource("Room 402", "office", location="Palaiseau",
                                     owner_user_id=owner.id, actor=admin)
    plan = await create_floor_plan("Office Plan 5", _make_image_bytes(), actor=admin)
    point = await add_map_point(plan.id, "Room 402", "room", 50, 50,
                                resource_id=resource.id, actor=admin)

    @ui.page("/pin-actions-edit-owner-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": False}
        await _show_pin_actions(area, state, owner, False, point)

    await user.open("/pin-actions-edit-owner-test")
    with pytest.raises(AssertionError):
        await user.should_see(t("edit_resource"))


async def test_pin_actions_hides_edit_button_for_floorplan_admin_without_booking_permission(
    user: User, monkeypatch, tmp_path
) -> None:
    """Reproducer for the exact permission-mixing mistake fixed for the
    Offer/Revoke buttons in b5ae458: a manage_floorplans-only admin (is_admin
    True from this file's perspective) must NOT see Edit — that button must
    gate on manage_bookings, not the is_admin parameter."""
    from not_dot_net.backend.booking_service import create_resource
    from not_dot_net.backend.floorplan_service import add_map_point
    from not_dot_net.backend.roles import RoleDefinition, roles_config
    from not_dot_net.frontend.floorplan import _show_pin_actions
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs
    import pytest

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)

    cfg = await roles_config.get()
    cfg.roles["floorplan_manager2"] = RoleDefinition(
        label="Floorplan Manager 2", permissions=["manage_floorplans"],
    )
    await roles_config.set(cfg)

    admin = await _make_admin()
    owner = await _create_staff_user(email="owner6@test.com")
    async with session_scope() as session:
        floorplan_manager = DbUser(
            id=uuid.uuid4(), email="fp-manager2@test.com", hashed_password="x",
            is_active=True, role="floorplan_manager2",
        )
        session.add(floorplan_manager)
        await session.commit()
        await session.refresh(floorplan_manager)

    resource = await create_resource("Room 403", "office", location="Palaiseau",
                                     owner_user_id=owner.id, actor=admin)
    plan = await create_floor_plan("Office Plan 6", _make_image_bytes(), actor=admin)
    point = await add_map_point(plan.id, "Room 403", "room", 50, 50,
                                resource_id=resource.id, actor=admin)

    @ui.page("/pin-actions-edit-floorplan-admin-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": False}
        await _show_pin_actions(area, state, floorplan_manager, True, point)

    await user.open("/pin-actions-edit-floorplan-admin-test")
    with pytest.raises(AssertionError):
        await user.should_see(t("edit_resource"))
