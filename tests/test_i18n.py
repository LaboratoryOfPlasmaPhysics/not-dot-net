from not_dot_net.frontend.i18n import TRANSLATIONS, _parse_accept_language, t


def test_all_keys_present_in_both_locales():
    en_keys = set(TRANSLATIONS["en"].keys())
    fr_keys = set(TRANSLATIONS["fr"].keys())
    assert en_keys == fr_keys, f"Missing in fr: {en_keys - fr_keys}, missing in en: {fr_keys - en_keys}"


def test_no_empty_translations():
    for locale, strings in TRANSLATIONS.items():
        for key, value in strings.items():
            assert value, f"{locale}.{key} is empty"


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
    shared_allowed = {"LPP Intranet"}
    for key in TRANSLATIONS["en"]:
        en = TRANSLATIONS["en"][key]
        fr = TRANSLATIONS["fr"][key]
        if en not in shared_allowed:
            assert en != fr, f"'{key}' has same value in en and fr: '{en}'"
