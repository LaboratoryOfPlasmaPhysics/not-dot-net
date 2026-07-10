from datetime import date, timedelta

from not_dot_net.frontend.bookings import (
    _default_booking_range,
    _minimum_booking_start,
    _normalize_booking_range,
    _qdate_option_date,
    _status_color,
    _truncate_booking_owner,
)


def test_minimum_booking_start_is_seven_days_after_today():
    today = date(2026, 5, 26)

    assert _minimum_booking_start(today) == today + timedelta(days=7)
    assert _minimum_booking_start(today, minimum_lead_days=3) == today + timedelta(days=3)


def test_default_booking_range_starts_at_minimum_date():
    today = date(2026, 5, 26)
    start = today + timedelta(days=7)

    assert _default_booking_range(today) == {
        "from": str(start),
        "to": str(start + timedelta(days=7)),
    }
    custom_start = today + timedelta(days=3)
    assert _default_booking_range(today, minimum_lead_days=3) == {
        "from": str(custom_start),
        "to": str(custom_start + timedelta(days=7)),
    }


def test_normalize_booking_range_keeps_valid_range():
    today = date(2026, 5, 26)
    value = {"from": "2026-06-02", "to": "2026-06-09"}

    assert _normalize_booking_range(value, today) == value


def test_normalize_booking_range_shifts_too_early_range_to_minimum_date():
    today = date(2026, 5, 26)

    assert _normalize_booking_range(
        {"from": "2026-05-26", "to": "2026-06-02"},
        today,
    ) == {"from": "2026-06-02", "to": "2026-06-09"}
    assert _normalize_booking_range(
        {"from": "2026-05-26", "to": "2026-06-02"},
        today,
        minimum_lead_days=3,
    ) == {"from": "2026-05-29", "to": "2026-06-05"}


def test_normalize_booking_range_falls_back_on_invalid_value():
    today = date(2026, 5, 26)

    assert _normalize_booking_range({"from": "bad"}, today) == _default_booking_range(today)


def test_qdate_option_date_uses_quasar_date_format():
    assert _qdate_option_date(date(2026, 6, 2)) == "2026/06/02"


def test_truncate_booking_owner_limits_display_name():
    assert _truncate_booking_owner("short@test.com") == "short@test.com"
    assert _truncate_booking_owner("lucas.bazin@lpp.polytechnique.fr") == (
        "lucas.bazin@lpp.polytech..."
    )


def test_status_color_maps_known_states():
    assert _status_color("available") == "positive"
    assert _status_color("out_of_service") == "negative"
    assert _status_color("ready") == "primary"
    # unknown falls back to grey
    assert _status_color("nonsense") == "grey"


def test_format_booking_period_shows_inclusive_last_day():
    """R-07: stored end_date is the exclusive hand-back day; cards must show
    the range the user actually picked (inclusive last day)."""
    from not_dot_net.frontend.bookings import _format_booking_period

    assert _format_booking_period(date(2026, 6, 10), date(2026, 6, 13)) == "2026-06-10 → 2026-06-12"


async def test_bookings_sites_read_from_vocabulary_registry():
    """TDD: after rewiring, sites in bookings come from the vocabulary registry,
    not from OrgConfig.sites. Seeding a custom vocabulary must be reflected."""
    from not_dot_net.backend.vocabularies import (
        vocabularies_config,
        VocabulariesConfig,
        StoredVocabulary,
        VocabularyTerm,
        resolve_terms,
    )

    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "sites": StoredVocabulary(
            key="sites",
            label={"en": "Sites"},
            terms=[
                VocabularyTerm(code="TestSite1", labels={"en": "TestSite1"}),
                VocabularyTerm(code="TestSite2", labels={"en": "TestSite2"}),
            ],
        ),
    }))

    sites = [t.code for t in await resolve_terms("sites")]
    assert sites == ["TestSite1", "TestSite2"]


from not_dot_net.frontend.bookings import _exclude_offices, _office_fields_visible, _resource_icon


def test_office_fields_visible_only_for_office_type():
    assert _office_fields_visible("office") is True
    assert _office_fields_visible("desktop") is False
    assert _office_fields_visible("laptop") is False


def test_resource_icon_maps_known_types():
    assert _resource_icon("desktop") == "desktop_windows"
    assert _resource_icon("laptop") == "laptop"
    assert _resource_icon("office") == "meeting_room"
    assert _resource_icon("nonsense") == "devices"


def test_exclude_offices_filters_out_office_resource_type():
    from types import SimpleNamespace

    resources = [
        SimpleNamespace(resource_type="desktop"),
        SimpleNamespace(resource_type="office"),
        SimpleNamespace(resource_type="laptop"),
    ]
    filtered = _exclude_offices(resources)
    assert [r.resource_type for r in filtered] == ["desktop", "laptop"]
