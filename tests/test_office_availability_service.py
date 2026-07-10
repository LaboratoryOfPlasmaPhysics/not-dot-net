import uuid
from datetime import date

from not_dot_net.backend.office_availability import OfficeAvailability, is_covered

_RESOURCE = uuid.uuid4()
_USER = uuid.uuid4()


def _window(start: str, end: str) -> OfficeAvailability:
    return OfficeAvailability(
        resource_id=_RESOURCE, start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end), offered_by=_USER,
    )


def test_is_covered_empty_window_list():
    assert is_covered([], date(2026, 8, 1), date(2026, 8, 10)) is False


def test_is_covered_exact_match():
    windows = [_window("2026-08-01", "2026-08-10")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is True


def test_is_covered_fully_inside_a_wider_window():
    windows = [_window("2026-07-25", "2026-08-20")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is True


def test_is_covered_partially_outside_returns_false():
    windows = [_window("2026-08-01", "2026-08-05")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is False


def test_is_covered_gap_between_windows_returns_false():
    windows = [_window("2026-08-01", "2026-08-04"), _window("2026-08-06", "2026-08-10")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is False


def test_is_covered_multiple_overlapping_windows_union():
    windows = [
        _window("2026-08-01", "2026-08-04"),
        _window("2026-08-03", "2026-08-07"),
        _window("2026-08-06", "2026-08-10"),
    ]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is True


def test_is_covered_unordered_windows_still_evaluated_correctly():
    windows = [_window("2026-08-06", "2026-08-10"), _window("2026-08-01", "2026-08-06")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is True
