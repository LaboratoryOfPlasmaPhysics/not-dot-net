from nicegui import app

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # Shell / nav
        "app_name": "LPP Intranet",
        "people": "People",
        "onboarding": "Onboarding",
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
        # Onboarding
        "new_person": "New Person",
        "name": "Name",
        "role_status": "Role / Status",
        "start_date": "Start Date",
        "note_optional": "Note (optional)",
        "submit": "Submit",
        "name_email_required": "Name and email are required",
        "onboarding_created": "Onboarding request created",
        "no_requests": "No onboarding requests yet.",
        "onboarding_requests": "Onboarding Requests",
        "starts": "starts {date}",
        # Roles
        "researcher": "Researcher",
        "phd_student": "PhD student",
        "intern": "Intern",
        "visitor": "Visitor",
        # Language
        "language": "Language",
    },
    "fr": {
        # Shell / nav
        "app_name": "LPP Intranet",
        "people": "Personnes",
        "onboarding": "Intégration",
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
        # Onboarding
        "new_person": "Nouvelle personne",
        "name": "Nom",
        "role_status": "Rôle / Statut",
        "start_date": "Date de début",
        "note_optional": "Note (facultatif)",
        "submit": "Envoyer",
        "name_email_required": "Le nom et l'e-mail sont requis",
        "onboarding_created": "Demande d'intégration créée",
        "no_requests": "Aucune demande d'intégration pour le moment.",
        "onboarding_requests": "Demandes d'intégration",
        "starts": "début {date}",
        # Roles
        "researcher": "Chercheur",
        "phd_student": "Doctorant",
        "intern": "Stagiaire",
        "visitor": "Visiteur",
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
