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
