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


def test_points_payload_contains_entry_per_point():
    from not_dot_net.frontend.floorplan import _points_payload

    points = [
        MapPoint(floor_plan_id=uuid.uuid4(), label="Room 101", kind="room", x=50, y=60),
        MapPoint(floor_plan_id=uuid.uuid4(), label="Plug 12", kind="wall_plug", x=120, y=200),
    ]
    payload = _points_payload(points)
    assert len(payload) == 2
    assert payload[0]["x"] == 50 and payload[0]["y"] == 60
    assert payload[1]["x"] == 120 and payload[1]["y"] == 200


def test_points_payload_escapes_label_special_characters():
    """Leaflet's bindTooltip sets tooltip content via innerHTML for string
    content, so an unescaped label would let an admin-entered pin label
    inject markup into every viewer's page."""
    from not_dot_net.frontend.floorplan import _points_payload

    points = [MapPoint(floor_plan_id=uuid.uuid4(), label="A&B <test>", kind="room", x=10, y=10)]
    payload = _points_payload(points)
    assert payload[0]["label"] == "A&amp;B &lt;test&gt;"


def test_points_payload_highlights_matching_point():
    from not_dot_net.frontend.floorplan import _points_payload

    target = MapPoint(floor_plan_id=uuid.uuid4(), label="Room 101", kind="room", x=50, y=60)
    other = MapPoint(floor_plan_id=uuid.uuid4(), label="Room 102", kind="room", x=90, y=60)
    payload = _points_payload([target, other], highlight_id=target.id)
    assert payload[0]["highlighted"] is True
    assert payload[1]["highlighted"] is False


def test_points_payload_colors_by_kind():
    from not_dot_net.frontend.floorplan import _KIND_COLOR, _points_payload

    points = [MapPoint(floor_plan_id=uuid.uuid4(), label="Plug 12", kind="wall_plug", x=1, y=1)]
    payload = _points_payload(points)
    assert payload[0]["color"] == _KIND_COLOR["wall_plug"]


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
        state = {"selected": plan, "highlight_id": None, "place_mode": "off", "editing_point_id": None}
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
        state = {"selected": plan, "highlight_id": None, "place_mode": "off", "editing_point_id": None}
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
        state = {"selected": plan, "highlight_id": None, "place_mode": "off", "editing_point_id": None}
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


async def test_add_pin_dialog_clears_resource_when_kind_switched_away_from_room(
    user: User, monkeypatch, tmp_path
) -> None:
    """Reproducer: hiding the resource picker when Kind != "room" is not
    enough — the underlying resource_select value survived the switch, so
    do_save could still submit a stale resource_id for a non-room pin. That
    recreates the exact dead-link state (resource_id set on a pin whose kind
    is never "room") the visibility fix was supposed to eliminate."""
    from nicegui import ElementFilter
    from nicegui import ui as nicegui_ui

    from not_dot_net.backend.booking_service import create_resource
    from not_dot_net.backend.floorplan_service import list_map_points
    from not_dot_net.frontend.floorplan import _show_add_pin_dialog
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)

    admin = await _make_admin()
    resource = await create_resource("Room 501", "office", location="Palaiseau", actor=admin)
    plan = await create_floor_plan("Office Plan 7", _make_image_bytes(), actor=admin)

    @ui.page("/add-pin-kind-switch-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": "place", "editing_point_id": None}
        await _show_add_pin_dialog(area, state, admin, True, plan.id, 42, 24)

    await user.open("/add-pin-kind-switch-test")
    await user.should_see(t("floorplan_link_resource"))

    with user.client:
        kind_select, resource_select = list(ElementFilter(kind=nicegui_ui.select))
        resource_select.value = resource.id
        kind_select.value = "desk"

        label_input = next(iter(ElementFilter(kind=nicegui_ui.input)))
        label_input.value = "Desk 501"

    user.find(t("save")).click()
    await user.should_see(t("floorplan_pin_added"))

    points = await list_map_points(plan.id)
    added = next(p for p in points if p.label == "Desk 501")
    assert added.resource_id is None


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
        state = {"selected": plan, "highlight_id": None, "place_mode": "off", "editing_point_id": None}
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
        state = {"selected": plan, "highlight_id": None, "place_mode": "off", "editing_point_id": None}
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
        state = {"selected": plan, "highlight_id": None, "place_mode": "off", "editing_point_id": None}
        await _show_pin_actions(area, state, floorplan_manager, True, point)

    await user.open("/pin-actions-edit-floorplan-admin-test")
    with pytest.raises(AssertionError):
        await user.should_see(t("edit_resource"))


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


async def test_render_plan_area_shows_kind_checkboxes_for_non_admin_viewer(
    user: User, monkeypatch, tmp_path
) -> None:
    """The layer-visibility checkbox row must render regardless of
    admin/editing state — the existing checkbox tests only ever exercised
    is_admin=True, leaving the plain-viewer path (no place-pin controls)
    uncovered."""
    from not_dot_net.frontend.floorplan import PIN_KINDS, _render_plan_area
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()
    staff = await _create_staff_user(email="staff-viewer@test.com")
    plan = await create_floor_plan("Layer Toggle Plan Non-Admin", _make_image_bytes(), actor=admin)

    @ui.page("/floorplan-layer-toggles-non-admin-test")
    async def page():
        area = ui.column()
        state = {
            "selected": plan, "highlight_id": None, "place_mode": "off",
            "editing_point_id": None,
        }
        await _render_plan_area(area, state, staff, False)

    await user.open("/floorplan-layer-toggles-non-admin-test")
    for kind in PIN_KINDS:
        await user.should_see(t(f"kind_{kind}"))

    checkboxes = list(user.find(kind=ui.checkbox).elements)
    assert len(checkboxes) == len(PIN_KINDS)
    assert all(cb.value is True for cb in checkboxes)


async def test_rechecking_a_kind_restores_it_to_visible_kinds_state(
    user: User, monkeypatch, tmp_path
) -> None:
    """Only the uncheck branch of on_kind_toggle (`elif not e.value and kind
    in kinds: kinds.remove(kind)`) was exercised previously. This covers the
    re-check branch (`if e.value and kind not in kinds: kinds.append(kind)`)
    by starting with a kind already excluded and checking it back on."""
    from nicegui import ElementFilter

    from not_dot_net.frontend.floorplan import PIN_KINDS, _render_plan_area
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)
    admin = await _make_admin()
    plan = await create_floor_plan("Layer Toggle Plan 3", _make_image_bytes(), actor=admin)

    state = {
        "selected": plan, "highlight_id": None, "place_mode": "off",
        "editing_point_id": None,
        "visible_kinds": [k for k in PIN_KINDS if k != "desk"],
    }

    @ui.page("/floorplan-layer-toggles-recheck-test")
    async def page():
        area = ui.column()
        await _render_plan_area(area, state, admin, True)

    await user.open("/floorplan-layer-toggles-recheck-test")
    with user.client:
        desk_checkbox = next(
            cb for cb in ElementFilter(kind=ui.checkbox) if cb.text == t("kind_desk")
        )
        assert desk_checkbox.value is False
        desk_checkbox.value = True

    assert "desk" in state["visible_kinds"]
