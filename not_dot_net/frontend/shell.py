import inspect
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from fastapi import Depends
from nicegui import app, ui

from not_dot_net.backend.db import User
from not_dot_net.backend.operations import operations_status
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.users import current_active_user_optional, dev_mode
from not_dot_net.frontend.admin_settings import render as render_settings
from not_dot_net.frontend.admin_ad_account import render as render_ad_accounts
from not_dot_net.frontend.audit_log import render as render_audit
from not_dot_net.frontend.bookings import render as render_bookings
from not_dot_net.frontend.floorplan import render as render_floorplan
from not_dot_net.frontend.operations import render as render_operations
from not_dot_net.frontend.pages import render as render_pages
from not_dot_net.frontend.services import render as render_services
from not_dot_net.frontend.directory import render as render_directory
from not_dot_net.frontend.dashboard import render as render_dashboard
from not_dot_net.frontend.user_management import render as render_user_management
from not_dot_net.frontend.new_request import render as render_new_request
from not_dot_net.frontend.i18n import SUPPORTED_LOCALES, apply_client_locale, get_locale, set_locale, t

logger = logging.getLogger("not_dot_net.shell")


_GUEST_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@dataclass
class GuestUser:
    """Lightweight stand-in for unauthenticated visitors."""
    id: uuid.UUID = field(default=_GUEST_UUID)
    email: str = "guest"
    role: str = ""
    full_name: str | None = None
    is_active: bool = False
    is_superuser: bool = False


@dataclass(frozen=True)
class Destination:
    """One place in the app. `place`: "bar" (tab bar), "admin" (Admin menu)
    or "hidden" (reached by a button or a link only)."""
    key: str
    label_key: str
    icon: str
    render: Callable[[Any, Optional[str]], Any]  # (user, person) -> refresh fn, maybe awaitable
    permission: str | None = None
    place: str = "bar"
    # Forms would lose typed input if re-rendered each time the tab is shown.
    refresh_on_show: bool = True


DESTINATIONS = [
    Destination("dashboard", "dashboard", "dashboard", lambda u, p: render_dashboard(u)),
    Destination("services", "services", "apps", lambda u, p: render_services(u)),
    Destination("people", "people", "people", lambda u, p: render_directory(u, focus_id=p)),
    Destination("bookings", "bookings", "event_available", lambda u, p: render_bookings(u)),
    Destination("floorplan", "floorplan", "map", lambda u, p: render_floorplan(u)),
    Destination("pages", "pages", "article", lambda u, p: render_pages(u)),
    Destination("new_request", "new_request", "add_circle", lambda u, p: render_new_request(u),
                permission="create_workflows", place="hidden", refresh_on_show=False),
    Destination("operations", "operations", "monitor_heart", lambda u, p: render_operations(),
                permission="manage_users", place="admin"),
    Destination("users", "user_management", "group", lambda u, p: render_user_management(u),
                permission="manage_users", place="admin"),
    Destination("ad_accounts", "ad_accounts", "manage_accounts", lambda u, p: render_ad_accounts(u),
                permission="manage_settings", place="admin", refresh_on_show=False),
    Destination("audit", "audit_log", "policy", lambda u, p: render_audit(),
                permission="view_audit_log", place="admin"),
    Destination("settings", "settings", "settings", lambda u, p: render_settings(u),
                permission="manage_settings", place="admin", refresh_on_show=False),
]


async def _allowed(user, logged_in: bool) -> list[Destination]:
    if not logged_in:
        return [d for d in DESTINATIONS if d.permission is None]
    return [d for d in DESTINATIONS if d.permission is None or await has_permissions(user, d.permission)]


async def _safe_actionable_count(user) -> int:
    """Actionable count for the badge, or 0 if the query fails.

    The badge runs on a 60s timer for every connected client; a transient DB
    error used to escape as an unhandled timer exception once a minute, per
    client, forever.
    """
    from not_dot_net.backend.workflow_service import get_actionable_count

    try:
        return await get_actionable_count(user)
    except Exception:
        logger.exception("Failed to compute actionable count for the badge")
        return 0


async def _safe_problem_count() -> int:
    """Operations problems for the Admin badge, or 0 if the query fails (same reason)."""
    try:
        return (await operations_status(app_dev_mode=dev_mode())).problems
    except Exception:
        logger.exception("Failed to compute operations status for the badge")
        return 0


def setup():
    @ui.page("/")
    async def main_page(
        user: Optional[User] = Depends(current_active_user_optional),
        tab: Optional[str] = None,
        person: Optional[str] = None,
    ):
        logged_in = user is not None
        effective_user = user or GuestUser()
        apply_client_locale()

        allowed = await _allowed(effective_user, logged_in)
        keys = {d.key for d in allowed}
        # Stored by key: a label stops matching once the language changes.
        saved = app.storage.user.get("active_tab")
        initial = next((k for k in (tab, saved) if k in keys), "dashboard")

        ui.colors(primary="#0F52AC")
        nav = _Navigation(allowed)
        _header(nav, effective_user, logged_in)
        _drawer(nav)
        await _panels(nav, effective_user, person, initial)
        if logged_in:
            _badges(nav, effective_user)
        return None


class _Navigation:
    """The tab bar, the Admin menu and the panels share one state: the active key."""

    def __init__(self, allowed: list[Destination]):
        self.allowed = allowed
        self.refreshers: dict[str, Callable[[], Awaitable[None]]] = {}
        self._refreshing: set[str] = set()
        self.tabs: ui.tabs | None = None
        self.dashboard_tab: ui.tab | None = None
        self.admin_button: ui.button | None = None
        self.drawer: ui.left_drawer | None = None

    def placed(self, place: str) -> list[Destination]:
        return [d for d in self.allowed if d.place == place]

    def go(self, key: str) -> None:
        self.tabs.set_value(key)
        if self.drawer is not None:
            self.drawer.hide()

    async def on_change(self, e) -> None:
        app.storage.user["active_tab"] = e.value
        self.label_admin_button(e.value)
        refresh = self.refreshers.get(e.value)
        if refresh is None or e.value in self._refreshing:
            return
        self._refreshing.add(e.value)
        try:
            await refresh()
        finally:
            self._refreshing.discard(e.value)

    def label_admin_button(self, key: str) -> None:
        """No tab is underlined in an admin place: the button says where you are."""
        if self.admin_button is None:
            return
        current = next((d for d in self.placed("admin") if d.key == key), None)
        self.admin_button.set_text(f"{t('admin')} · {t(current.label_key)}" if current else t("admin"))


def _header(nav: _Navigation, user, logged_in: bool) -> None:
    with ui.header().classes("row items-center justify-between px-4 no-wrap").style(
        "background-color: #0F52AC"
    ):
        ui.button(icon="menu", on_click=lambda: nav.drawer.toggle()).props(
            f'flat color=white aria-label="{t("open_menu")}"'
        ).classes("xs")
        ui.label(t("app_name")).classes("text-h6 text-white text-weight-light")
        with ui.tabs().props("outside-arrows mobile-arrows").classes("ml-4 min-w-0 flex-1 gt-xs") as tabs:
            for d in nav.placed("bar"):
                tab = ui.tab(d.key, label=t(d.label_key), icon=d.icon)
                if d.key == "dashboard":
                    nav.dashboard_tab = tab
        nav.tabs = tabs
        tabs.on_value_change(nav.on_change)
        with ui.row().classes("items-center no-wrap"):
            _admin_menu(nav)
            _language_toggle()
            _account_menu(user, logged_in)


def _admin_menu(nav: _Navigation) -> None:
    admin = nav.placed("admin")
    if not admin:
        return
    with ui.button(t("admin"), icon="admin_panel_settings").props("flat color=white").classes("gt-xs") as button:
        with ui.menu():
            for d in admin:
                with ui.menu_item(on_click=lambda key=d.key: nav.go(key)):
                    with ui.row().classes("items-center gap-3 no-wrap"):
                        ui.icon(d.icon)
                        ui.label(t(d.label_key))
    nav.admin_button = button


def _language_toggle() -> None:
    def on_change(e):
        set_locale(e.value)
        ui.run_javascript("window.location.reload()")

    ui.toggle(list(SUPPORTED_LOCALES), value=get_locale(), on_change=on_change).props(
        "unelevated dense color=primary text-color=white toggle-color=white toggle-text-color=primary"
    )


def _account_menu(user, logged_in: bool) -> None:
    if not logged_in:
        ui.button(t("log_in"), icon="login", on_click=lambda: ui.navigate.to("/login")).props("flat color=white")
        return
    with ui.button(icon="person").props(f'flat color=white aria-label="{t("my_account")}"').tooltip(t("my_account")):
        with ui.menu():
            ui.menu_item(t("my_profile"), on_click=lambda: ui.navigate.to(f"/?tab=people&person={user.id}"))
            if user.ldap_username:
                ui.menu_item(t("change_my_password"), on_click=lambda: ui.navigate.to("/change-password"))
            ui.menu_item(t("logout"), on_click=lambda: ui.navigate.to("/logout"))


def _drawer(nav: _Navigation) -> None:
    """Phones get a side menu: no tab label fits at 390 px."""
    with ui.left_drawer(value=False).props("overlay behavior=mobile bordered") as drawer:
        with ui.list().classes("w-full"):
            for group in (nav.placed("bar"), nav.placed("admin")):
                if group and group[0].place == "admin":
                    ui.separator()
                for d in group:
                    with ui.item(on_click=lambda key=d.key: nav.go(key)):
                        with ui.item_section().props("avatar"):
                            ui.icon(d.icon)
                        ui.item_section(t(d.label_key))
    nav.drawer = drawer


async def _panels(nav: _Navigation, user, person: Optional[str], initial: str) -> None:
    """Every panel is rendered once at page load; showing a tab re-runs its refresh."""
    with ui.tab_panels(nav.tabs, value=initial).classes("w-full"):
        for d in nav.allowed:
            with ui.tab_panel(d.key):
                result = d.render(user, person)
                if inspect.isawaitable(result):
                    result = await result
                if d.refresh_on_show and result is not None:
                    nav.refreshers[d.key] = result
    nav.label_admin_button(initial)


def _badges(nav: _Navigation, user) -> None:
    last_count: int | None = None
    dashboard_label = t("dashboard")
    sees_operations = any(d.key == "operations" for d in nav.allowed)
    admin_badge = None
    if nav.admin_button is not None and sees_operations:
        with nav.admin_button:
            admin_badge = ui.badge("", color="negative").props("floating")
        admin_badge.set_visibility(False)

    async def update():
        nonlocal last_count
        try:
            count = await _safe_actionable_count(user)
            nav.dashboard_tab._props["label"] = f"{dashboard_label} ({count})" if count > 0 else dashboard_label
            nav.dashboard_tab.update()
            changed = last_count is not None and count != last_count
            last_count = count
            if changed and nav.tabs.value == "dashboard":
                await nav.refreshers["dashboard"]()
            if admin_badge is not None:
                problems = await _safe_problem_count()
                admin_badge.set_text(str(problems))
                admin_badge.set_visibility(problems > 0)
            title = f"({count}) {t('app_name')}" if count > 0 else t("app_name")
            await ui.run_javascript(f"document.title = {title!r}")
        except (RuntimeError, TimeoutError):
            # Client vanished mid-update (disconnect, reload) — expected.
            pass

    ui.timer(60, update)
    ui.timer(0, update, once=True)
