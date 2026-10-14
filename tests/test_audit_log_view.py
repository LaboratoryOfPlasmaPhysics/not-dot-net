"""Tests for the audit log view helpers — severity classification and
relative-time labels. These are pure functions, no DB needed."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from not_dot_net.frontend import audit_log as al


@pytest.fixture(autouse=True)
def _stub_t(monkeypatch):
    """Bypass NiceGUI's storage-bound locale lookup in these unit tests by
    rendering t() keys as a deterministic English-shaped string."""
    keys = {
        "audit_time_yesterday": "yesterday {time}",
        "audit_time_before_yesterday": "before yesterday {time}",
        "audit_time_days_ago": "{days}d ago",
    }

    def fake_t(key, **kw):
        text = keys.get(key, key)
        return text.format(**kw) if kw else text

    monkeypatch.setattr(al, "t", fake_t)


def _ev(category, action, *, detail=None, metadata=None):
    return SimpleNamespace(
        category=category,
        action=action,
        detail=detail,
        metadata_json=metadata,
    )


# --- severity ---------------------------------------------------------------

def test_regular_login_is_normal():
    assert al._audit_severity(_ev("auth", "login")) == "green"


def test_superuser_login_is_sensitive_not_critical():
    """I3: every admin login was red, so 21 of 21 critical rows were logins."""
    ev = _ev("auth", "login", metadata={"is_superuser": True, "success": True})
    assert al._audit_severity(ev) == "orange"


def test_failed_superuser_login_is_critical_via_metadata():
    ev = _ev("auth", "login", metadata={"is_superuser": True, "success": False})
    assert al._audit_severity(ev) == "red"


def test_settings_update_role_is_critical():
    assert al._audit_severity(_ev("settings", "update_role")) == "red"


def test_settings_update_roles_section_is_critical():
    ev = _ev("settings", "update", detail="section=roles")
    assert al._audit_severity(ev) == "red"


def test_settings_update_ldap_section_is_critical():
    ev = _ev("settings", "update", detail="section=ldap")
    assert al._audit_severity(ev) == "red"


def test_settings_update_other_section_is_sensitive():
    ev = _ev("settings", "update", detail="section=mail")
    assert al._audit_severity(ev) == "orange"


def test_settings_update_with_lookalike_section_is_not_critical():
    """Regression: substring matching used to flag any detail containing
    'section=roles' anywhere (e.g. a hypothetical 'section=roles_lockout'
    or noise in the detail). Severity is now driven by exact (cat, action,
    section) — unknown sections fall back to the generic 'orange'."""
    ev = _ev("settings", "update", detail="section=roles_extra")
    assert al._audit_severity(ev) == "orange"


def test_settings_export_is_sensitive():
    assert al._audit_severity(_ev("settings", "export")) == "orange"


def test_personal_data_download_is_sensitive():
    assert al._audit_severity(_ev("personal_data", "download")) == "orange"


def test_workflow_resend_notification_is_sensitive():
    assert al._audit_severity(_ev("workflow", "resend_notification")) == "orange"


def test_user_tenure_actions_are_sensitive():
    for action in ("add_tenure", "update_tenure", "delete_tenure"):
        assert al._audit_severity(_ev("user", action)) == "orange"


def test_user_update_with_role_change_is_critical():
    ev = _ev("user", "update", detail="fields=role,name",
             metadata={"changes": {"role": {"old": "user", "new": "admin"}}})
    assert al._audit_severity(ev) == "red"


def test_user_update_without_role_change_is_normal():
    """Regression: substring 'role' in detail used to match any field name
    containing 'role' (e.g. 'controller', 'payroll_id'). We now look at
    metadata['changes'] keys instead."""
    ev = _ev("user", "update", detail="fields=controller,payroll_id",
             metadata={"changes": {"controller": {}, "payroll_id": {}}})
    assert al._audit_severity(ev) == "green"


def test_unknown_event_defaults_to_normal():
    assert al._audit_severity(_ev("booking", "create")) == "green"


# --- metadata display -------------------------------------------------------

def test_format_metadata_empty_returns_empty_string():
    assert al._format_metadata(None) == ""
    assert al._format_metadata({}) == ""


def test_format_metadata_renders_stable_json():
    assert al._format_metadata({"b": 2, "a": "x"}) == '{"a": "x", "b": 2}'


def test_flatten_metadata_handles_nested_values():
    assert al._flatten_metadata(
        {"ip": "127.0.0.1", "network": {"client_host": "10.0.0.42"}}
    ) == [
        ("ip", "127.0.0.1"),
        ("network.client_host", "10.0.0.42"),
    ]


def test_metadata_detail_html_escapes_values():
    html = al._metadata_detail_html({"user_agent": "<script>"})
    assert "&lt;script&gt;" in html
    assert "<script>" not in html


# --- relative time labels ---------------------------------------------------

# Naive UTC, like the database. Labels show the viewer's zone (Europe/Paris,
# UTC+2 in May), so 09:15 UTC reads 11:15.
NOW = datetime(2026, 5, 4, 14, 30, 0)


def test_relative_label_none_returns_empty():
    assert al._relative_time_label(None, now=NOW) == ""


def test_relative_label_today_shows_time_only():
    dt = datetime(2026, 5, 4, 9, 15, 0)
    # current locale is English by default in tests
    assert al._relative_time_label(dt, now=NOW) == "11:15"


def test_relative_label_yesterday_uses_i18n():
    """Regression: the label used to hardcode the French word 'hier' even
    when running in English. Now goes through the t() pipeline."""
    dt = datetime(2026, 5, 3, 9, 15, 0)
    assert al._relative_time_label(dt, now=NOW) == "yesterday 11:15"


def test_relative_label_two_days_ago_uses_i18n():
    dt = datetime(2026, 5, 2, 9, 15, 0)
    assert al._relative_time_label(dt, now=NOW) == "before yesterday 11:15"


def test_relative_label_older_uses_days_count():
    dt = datetime(2026, 4, 24, 12, 0, 0)
    assert al._relative_time_label(dt, now=NOW) == "10d ago"


def test_relative_label_counts_days_in_the_viewer_zone():
    """22:30 UTC on May 3 is 00:30 on May 4 in Paris: today, not yesterday."""
    dt = datetime(2026, 5, 3, 22, 30, 0)
    assert al._relative_time_label(dt, now=NOW) == "00:30"
