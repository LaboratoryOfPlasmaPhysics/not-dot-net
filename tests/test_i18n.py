import string
from pathlib import Path

import not_dot_net.frontend.i18n as i18n_module
from not_dot_net.frontend.i18n import TRANSLATIONS, _parse_accept_language, t

# Resolved at import time: other tests use runpy, which can leave
# `import not_dot_net.frontend...` failing mid-suite.
I18N_SOURCE = Path(i18n_module.__file__)

SECURITY_MESSAGE_KEYS = {
    "invalid_credentials",
    "auth_error",
    "session_expired",
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
    shared_allowed = {"LPP Intranet", "Type", "Permanent", "Description", "Note", "Action", "CPU", "RAM", "GPU", "Pages", "Import / Export", "Photo", "Justification", "RIB", "Notifications", "Permissions", "Local", "AD/LDAP", "Super", "Normal", "sAMAccountName", "Source", "UID", "Email", "Code", "OK"}
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
