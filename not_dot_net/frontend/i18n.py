import logging

from nicegui import app

logger = logging.getLogger(__name__)

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
        # Workflow
        "dashboard": "Dashboard",
        "new_request": "New Request",
        "my_requests": "My Requests",
        "awaiting_action": "Awaiting My Action",
        "no_requests": "No requests yet",
        "no_pending": "Nothing pending",
        "workflow_type": "Type",
        "current_step": "Current Step",
        "created_by": "Created by",
        "created_at": "Created",
        "approve": "Approve",
        "reject": "Reject",
        "approved": "Approved",
        "rejected": "Rejected",
        "completed": "Completed",
        "in_progress": "In Progress",
        "comment": "Comment (optional)",
        "select_workflow": "Select a workflow to start",
        "request_created": "Request created",
        "step_submitted": "Step submitted",
        "draft_saved": "Draft saved",
        "required_field": "This field is required",
        "token_expired": "This link has expired or is invalid",
        "token_welcome": "Please complete the form below",
        "save_draft": "Save Draft",
        "file_upload": "Upload File",
        "target_person": "Target Person",
        "all_types": "All types",
        "all_statuses": "All statuses",
        "filter": "Filter",
        "start_date": "Start Date",
        "end_date": "End Date",
        "since": "Since",
        "until": "Until",
        "permanent": "Permanent",
        "progress": "Progress",
        # Audit
        "audit_log": "Audit Log",
        "category": "Category",
        "action": "Action",
        "actor": "Actor",
        "target": "Target",
        "detail": "Detail",
        "time": "Time",
        "no_events": "No events",
        # Bookings
        "bookings": "Bookings",
        "resources": "Resources",
        "add_resource": "Add Resource",
        "edit_resource": "Edit Resource",
        "resource_name": "Name",
        "resource_type": "Type",
        "resource_location": "Location",
        "description": "Description",
        "desktop": "Desktop",
        "laptop": "Laptop",
        "book": "Book",
        "cancel_booking": "Cancel Booking",
        "my_bookings": "My Bookings",
        "no_bookings": "No bookings",
        "booking_created": "Booking confirmed",
        "booking_cancelled": "Booking cancelled",
        "resource_created": "Resource added",
        "resource_updated": "Resource updated",
        "resource_deleted": "Resource deleted",
        "booked_by": "Booked by",
        "available": "Available",
        "note": "Note",
        "specs": "Specs",
        "cpu": "CPU",
        "ram": "RAM",
        "hdd": "Storage",
        "gpu": "GPU",
        "os": "Operating System",
        "software": "Software",
        "manage_software": "OS & Software",
        "add_os": "Add OS",
        "add_software": "Add Software",
        "settings": "Settings",
        "reset_defaults": "Reset to Defaults",
        "settings_saved": "Settings saved",
        "settings_reset": "Settings reset to defaults",
        # Language
        "language": "Language",
        # Pages
        "pages": "Pages",
        "new_page": "New Page",
        "edit_page": "Edit Page",
        "page_title": "Title",
        "page_slug": "Slug",
        "page_content": "Content (Markdown)",
        "page_sort_order": "Sort Order",
        "page_published": "Published",
        "page_saved": "Page saved",
        "page_deleted": "Page deleted",
        "page_not_found": "Page not found",
        "page_draft": "Draft",
        "confirm_delete_page": "Delete this page?",
        # Workflow detail
        "back_to_dashboard": "Back to dashboard",
        "requested_by": "Requested by",
        "via_token": "via token link",
        "show_data": "Show submitted data",
        "hide_data": "Hide data",
        "take_action": "Take Action",
        "waiting_since": "Waiting since",
        "your_action_needed": "Your action needed",
        "request_detail": "Request Detail",
        "view_detail": "View Detail",
        "age": "Age",
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
        # Workflow
        "dashboard": "Tableau de bord",
        "new_request": "Nouvelle demande",
        "my_requests": "Mes demandes",
        "awaiting_action": "En attente de mon action",
        "no_requests": "Aucune demande",
        "no_pending": "Rien en attente",
        "workflow_type": "Type",
        "current_step": "Étape en cours",
        "created_by": "Créé par",
        "created_at": "Créé le",
        "approve": "Approuver",
        "reject": "Rejeter",
        "approved": "Approuvé",
        "rejected": "Rejeté",
        "completed": "Terminé",
        "in_progress": "En cours",
        "comment": "Commentaire (optionnel)",
        "select_workflow": "Sélectionnez un workflow à lancer",
        "request_created": "Demande créée",
        "step_submitted": "Étape envoyée",
        "draft_saved": "Brouillon enregistré",
        "required_field": "Ce champ est obligatoire",
        "token_expired": "Ce lien a expiré ou est invalide",
        "token_welcome": "Veuillez remplir le formulaire ci-dessous",
        "save_draft": "Enregistrer le brouillon",
        "file_upload": "Téléverser un fichier",
        "target_person": "Personne concernée",
        "all_types": "Tous les types",
        "all_statuses": "Tous les statuts",
        "filter": "Filtrer",
        "start_date": "Date de début",
        "end_date": "Date de fin",
        "since": "Depuis",
        "until": "Jusqu'au",
        "permanent": "Permanent",
        "progress": "Progression",
        # Audit
        "audit_log": "Journal d'audit",
        "category": "Catégorie",
        "action": "Action",
        "actor": "Acteur",
        "target": "Cible",
        "detail": "Détail",
        "time": "Heure",
        "no_events": "Aucun événement",
        # Bookings
        "bookings": "Réservations",
        "resources": "Ressources",
        "add_resource": "Ajouter une ressource",
        "edit_resource": "Modifier la ressource",
        "resource_name": "Nom",
        "resource_type": "Type",
        "resource_location": "Emplacement",
        "description": "Description",
        "desktop": "Poste fixe",
        "laptop": "Portable",
        "book": "Réserver",
        "cancel_booking": "Annuler la réservation",
        "my_bookings": "Mes réservations",
        "no_bookings": "Aucune réservation",
        "booking_created": "Réservation confirmée",
        "booking_cancelled": "Réservation annulée",
        "resource_created": "Ressource ajoutée",
        "resource_updated": "Ressource mise à jour",
        "resource_deleted": "Ressource supprimée",
        "booked_by": "Réservé par",
        "available": "Disponible",
        "note": "Note",
        "specs": "Caractéristiques",
        "cpu": "CPU",
        "ram": "RAM",
        "hdd": "Stockage",
        "gpu": "GPU",
        "os": "Système d'exploitation",
        "software": "Logiciels",
        "manage_software": "OS & Logiciels",
        "add_os": "Ajouter un OS",
        "add_software": "Ajouter un logiciel",
        "settings": "Paramètres",
        "reset_defaults": "Réinitialiser",
        "settings_saved": "Paramètres enregistrés",
        "settings_reset": "Paramètres réinitialisés",
        # Language
        "language": "Langue",
        # Pages
        "pages": "Pages",
        "new_page": "Nouvelle page",
        "edit_page": "Modifier la page",
        "page_title": "Titre",
        "page_slug": "Identifiant URL",
        "page_content": "Contenu (Markdown)",
        "page_sort_order": "Ordre d'affichage",
        "page_published": "Publiée",
        "page_saved": "Page enregistrée",
        "page_deleted": "Page supprimée",
        "page_not_found": "Page introuvable",
        "page_draft": "Brouillon",
        "confirm_delete_page": "Supprimer cette page ?",
        # Workflow detail
        "back_to_dashboard": "Retour au tableau de bord",
        "requested_by": "Demandé par",
        "via_token": "via lien de jeton",
        "show_data": "Afficher les données",
        "hide_data": "Masquer les données",
        "take_action": "Agir",
        "waiting_since": "En attente depuis",
        "your_action_needed": "Action requise",
        "request_detail": "Détail de la demande",
        "view_detail": "Voir le détail",
        "age": "Ancienneté",
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


_ALL_KEYS: frozenset[str] = frozenset(TRANSLATIONS[DEFAULT_LOCALE].keys())


def validate_translations() -> list[str]:
    """Check that all locales define the same keys. Returns list of problems."""
    problems = []
    for locale, trans in TRANSLATIONS.items():
        missing = _ALL_KEYS - trans.keys()
        extra = trans.keys() - _ALL_KEYS
        for k in missing:
            problems.append(f"[{locale}] missing key: {k}")
        for k in extra:
            problems.append(f"[{locale}] extra key (not in {DEFAULT_LOCALE}): {k}")
    for problem in problems:
        logger.warning("i18n: %s", problem)
    return problems


def t(key: str, **kwargs) -> str:
    """Translate a key to the current locale. Supports {name} placeholders."""
    locale = get_locale()
    text = TRANSLATIONS.get(locale, TRANSLATIONS[DEFAULT_LOCALE]).get(key)
    if text is None:
        logger.warning("i18n: unknown key '%s'", key)
        text = key
    if kwargs:
        text = text.format(**kwargs)
    return text
