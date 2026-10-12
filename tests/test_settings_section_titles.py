"""Settings section titles are translated (review 2026-10-12 V-list).

They came from the English `label=` of each ConfigSection, so the French
settings page mixed languages.
"""

from not_dot_net.backend.app_config import get_registry
from not_dot_net.backend.models import register_all_models
from not_dot_net.frontend.admin_settings import _SECTIONS_WITH_OWN_EDITOR, section_title_key
from not_dot_net.frontend.i18n import SUPPORTED_LOCALES, TRANSLATIONS


def test_every_generic_section_has_a_title_in_every_locale():
    register_all_models()
    # Other tests register throwaway sections in the same global registry.
    app_sections = {p for p, sec in get_registry().items() if sec.schema.__module__.startswith("not_dot_net.")}
    generic = app_sections - _SECTIONS_WITH_OWN_EDITOR
    assert generic
    for locale in SUPPORTED_LOCALES:
        missing = [p for p in generic if section_title_key(p) not in TRANSLATIONS[locale]]
        assert missing == [], f"{locale}: {missing}"
