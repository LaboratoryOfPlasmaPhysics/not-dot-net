import string
from pathlib import Path

import not_dot_net.frontend.i18n as i18n_module
from not_dot_net.frontend.i18n import TRANSLATIONS, _parse_accept_language, t

# Resolved at import time: other tests use runpy, which can leave
# `import not_dot_net.frontend...` failing mid-suite.
I18N_SOURCE = Path(i18n_module.__file__)

SECURITY_MESSAGE_KEYS = {
    "invalid_credentials",
    "token_expired",
    "invalid_code",
    "too_many_attempts",
    "access_denied",
    "permission_denied",
    "import_invalid_json",
    "import_failed",
}


def _placeholders(text: str) -> set[str]:
    return {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(text)
        if field_name is not None
    }


def test_placeholder_named_key_is_interpolated():
    """Review 2026-10-12: `t(name, key=...)` raised TypeError ("multiple values
    for argument 'key'"), crashing the roles panel and vocabulary delete."""
    assert "staff" in t("role_deleted", key="staff")


def test_all_keys_present_in_both_locales():
    en_keys = set(TRANSLATIONS["en"].keys())
    fr_keys = set(TRANSLATIONS["fr"].keys())
    assert en_keys == fr_keys, f"Missing in fr: {en_keys - fr_keys}, missing in en: {fr_keys - en_keys}"


def test_no_empty_translations():
    for locale, strings in TRANSLATIONS.items():
        for key, value in strings.items():
            assert value, f"{locale}.{key} is empty"


def test_translation_placeholders_match_across_locales():
    default_strings = TRANSLATIONS["en"]
    for locale, strings in TRANSLATIONS.items():
        for key, value in strings.items():
            assert _placeholders(value) == _placeholders(default_strings[key]), (
                f"{locale}.{key} placeholders differ from en"
            )


def test_security_messages_do_not_interpolate_runtime_details():
    for locale, strings in TRANSLATIONS.items():
        for key in SECURITY_MESSAGE_KEYS:
            assert _placeholders(strings[key]) == set(), (
                f"{locale}.{key} should not expose runtime details"
            )


def test_parse_accept_language_french():
    assert _parse_accept_language("fr-FR,fr;q=0.9,en;q=0.8") == "fr"


def test_parse_accept_language_english():
    assert _parse_accept_language("en-US,en;q=0.9") == "en"


def test_parse_accept_language_empty():
    assert _parse_accept_language("") == "en"


def test_parse_accept_language_unknown_falls_back():
    assert _parse_accept_language("de-DE,de;q=0.9") == "en"


def test_t_with_placeholder():
    text = TRANSLATIONS["en"]["confirm_delete"]
    assert "{name}" in text
    assert "Alice" in text.format(name="Alice")


def test_fr_translations_are_not_english():
    shared_allowed = {"LPP Intranet", "Type", "Permanent", "Description", "Note", "Action", "CPU", "RAM", "GPU", "Pages", "Import / Export", "Photo", "Justification", "RIB", "Notifications", "Permissions", "Local", "AD/LDAP", "Super", "Normal", "sAMAccountName", "Source", "UID", "Email", "Code", "OK", "Workflows", "LDAP / Active Directory", "Services", "Documentation"}
    for key in TRANSLATIONS["en"]:
        en = TRANSLATIONS["en"][key]
        fr = TRANSLATIONS["fr"][key]
        if en not in shared_allowed:
            assert en != fr, f"'{key}' has same value in en and fr: '{en}'"


def test_no_duplicate_keys_in_any_locale():
    """A repeated key in a dict literal silently keeps the LAST definition.

    Adding "returning_person" a second time made t("returning_person", name=X)
    render an unrelated label and drop the placeholder — with no error anywhere.
    """
    import ast
    import collections

    tree = ast.parse(I18N_SOURCE.read_text())
    duplicates = {}
    for node in ast.walk(tree):
        target = getattr(node, "target", None)
        if not isinstance(node, ast.AnnAssign) or getattr(target, "id", "") != "TRANSLATIONS":
            continue
        for locale_node, table in zip(node.value.keys, node.value.values):
            names = [k.value for k in table.keys if isinstance(k, ast.Constant)]
            repeated = sorted(k for k, n in collections.Counter(names).items() if n > 1)
            if repeated:
                duplicates[locale_node.value] = repeated

    # "description" and "office" are pre-existing and already on the backlog.
    known = {"description", "office"}
    unexpected = {loc: [k for k in keys if k not in known] for loc, keys in duplicates.items()}
    unexpected = {loc: keys for loc, keys in unexpected.items() if keys}
    assert not unexpected, f"duplicate translation keys: {unexpected}"



# --- Review 2026-10-12 V1: Quasar's own strings (table paging, date pickers)
# stayed English in French: ui.run had no language and it is per user here.


def test_quasar_lang_script_switches_french():
    script = i18n_module.quasar_lang_script("fr")
    assert "/static/lang/fr.umd.prod.js" in script
    assert "Quasar.Lang.set(Quasar.Lang.fr)" in script


def test_quasar_lang_script_leaves_english_alone():
    assert i18n_module.quasar_lang_script("en") is None


async def test_quasar_lang_pack_is_served(user):
    import re
    src = re.search(r"'(/_nicegui/[^']+)'", i18n_module.quasar_lang_script("fr")).group(1)
    await user.open("/login")
    response = await user.http_client.get(src)
    assert response.status_code == 200
    assert "Quasar.Lang.fr" in response.text



# --- Review 2026-10-12 V5: the backend is all naive UTC; pages show the
# viewer's own timezone (from the browser), Europe/Paris until it is known.


def test_local_time_converts_naive_utc_to_the_viewer_zone():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    utc = datetime(2026, 8, 24, 15, 6)
    assert i18n_module.local_time(utc, ZoneInfo("Europe/Paris")).strftime("%H:%M") == "17:06"
    assert i18n_module.local_time(utc, ZoneInfo("America/New_York")).strftime("%H:%M") == "11:06"


def test_local_time_keeps_aware_values_absolute():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    aware = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
    assert i18n_module.local_time(aware, ZoneInfo("Europe/Paris")).strftime("%H:%M") == "13:00"


def test_viewer_timezone_defaults_outside_a_page():
    assert str(i18n_module.viewer_timezone()) == "Europe/Paris"


def test_format_datetime_handles_missing_values():
    assert i18n_module.format_datetime(None) == ""


def test_format_short_date_uses_local_day_for_datetimes():
    """23:30 UTC on Aug 24 is already Aug 25 in Paris."""
    from datetime import date, datetime
    assert i18n_module.format_short_date(datetime(2026, 8, 24, 23, 30)) == "Aug 25"
    assert i18n_module.format_short_date(date(2026, 8, 24)) == "Aug 24"
