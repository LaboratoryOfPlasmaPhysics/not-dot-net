from nicegui import app

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # Shell / nav
        "app_name": "LPP Intranet",
        "people": "People",
        "my_profile": "My Profile",
        "logout": "Logout",
        # Login
        "email": "Email",
        "password": "Password",
        "log_in": "Log in",
        "invalid_credentials": "Invalid email or password",
        "auth_error": "Auth server error",
        # Directory
        "search_placeholder": "Search by name, team, office, email...",
        "office": "Office",
        "phone": "Phone",
        "status": "Status",
        "title": "Title",
        "full_name": "Full Name",
        "team": "Team",
        "edit": "Edit",
        "delete": "Delete",
        "save": "Save",
        "cancel": "Cancel",
        "saved": "Saved",
        "confirm_delete": "Delete {name}?",
        "deleted": "Deleted {name}",
        # Common
        "name": "Name",
        "submit": "Submit",
        # Language
        "language": "Language",
    },
    "fr": {
        # Shell / nav
        "app_name": "LPP Intranet",
        "people": "Personnes",
        "my_profile": "Mon profil",
        "logout": "Déconnexion",
        # Login
        "email": "E-mail",
        "password": "Mot de passe",
        "log_in": "Connexion",
        "invalid_credentials": "E-mail ou mot de passe invalide",
        "auth_error": "Erreur du serveur d'authentification",
        # Directory
        "search_placeholder": "Rechercher par nom, équipe, bureau, e-mail...",
        "office": "Bureau",
        "phone": "Téléphone",
        "status": "Statut",
        "title": "Titre",
        "full_name": "Nom complet",
        "team": "Équipe",
        "edit": "Modifier",
        "delete": "Supprimer",
        "save": "Enregistrer",
        "cancel": "Annuler",
        "saved": "Enregistré",
        "confirm_delete": "Supprimer {name}\u202f?",
        "deleted": "{name} supprimé",
        # Common
        "name": "Nom",
        "submit": "Envoyer",
        # Language
        "language": "Langue",
    },
}

SUPPORTED_LOCALES = ("en", "fr")
DEFAULT_LOCALE = "en"


def get_locale() -> str:
    """Get current locale from user storage, or detect from browser."""
    stored = app.storage.user.get("locale")
    if stored in SUPPORTED_LOCALES:
        return stored
    # Detect from Accept-Language header
    try:
        accept = app.storage.browser.get("accept_language", "")
        if not accept:
            from starlette.requests import Request
            request: Request = app.storage.browser.get("request")
            if request:
                accept = request.headers.get("accept-language", "")
    except Exception:
        accept = ""
    locale = _parse_accept_language(accept)
    app.storage.user["locale"] = locale
    return locale


def _parse_accept_language(header: str) -> str:
    """Extract best matching locale from Accept-Language header."""
    if not header:
        return DEFAULT_LOCALE
    for part in header.split(","):
        lang = part.split(";")[0].strip().lower()
        if lang.startswith("fr"):
            return "fr"
        if lang.startswith("en"):
            return "en"
    return DEFAULT_LOCALE


def set_locale(locale: str) -> None:
    """Set locale in user storage."""
    if locale in SUPPORTED_LOCALES:
        app.storage.user["locale"] = locale


def t(key: str, **kwargs) -> str:
    """Translate a key to the current locale. Supports {name} placeholders."""
    locale = get_locale()
    text = TRANSLATIONS.get(locale, TRANSLATIONS[DEFAULT_LOCALE]).get(key, key)
    if kwargs:
        text = text.format(**kwargs)
    return text
