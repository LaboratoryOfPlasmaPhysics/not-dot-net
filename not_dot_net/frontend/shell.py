import logging
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from fastapi import Depends
from nicegui import app, ui

from not_dot_net.backend.db import User
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.users import current_active_user_optional
from not_dot_net.frontend.admin_settings import render as render_settings
from not_dot_net.frontend.admin_ad_account import render as render_ad_accounts
from not_dot_net.frontend.audit_log import render as render_audit
from not_dot_net.frontend.bookings import render as render_bookings
from not_dot_net.frontend.floorplan import render as render_floorplan
from not_dot_net.frontend.pages import render as render_pages
from not_dot_net.frontend.directory import render as render_directory
from not_dot_net.frontend.dashboard import render as render_dashboard
from not_dot_net.frontend.user_management import render as render_user_management
from not_dot_net.frontend.new_request import render as render_new_request
from not_dot_net.frontend.i18n import SUPPORTED_LOCALES, get_locale, set_locale, t

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


def setup():
    @ui.page("/")
    async def main_page(
        user: Optional[User] = Depends(current_active_user_optional),
        tab: Optional[str] = None,
    ):
        logged_in = user is not None
        effective_user = user or GuestUser()

        locale = get_locale()
        people_label = t("people")
        dashboard_label = t("dashboard")
        new_request_label = t("new_request")
        bookings_label = t("bookings")
        floorplan_label = t("floorplan")
        pages_label = t("pages")
        audit_label = t("audit_log")
        settings_label = t("settings")
        ad_accounts_label = t("ad_accounts")
        users_label = t("user_management")

        can_create = await has_permissions(effective_user, "create_workflows") if logged_in else False
        can_audit = await has_permissions(effective_user, "view_audit_log") if logged_in else False
        is_admin = await has_permissions(effective_user, "manage_settings") if logged_in else False
        is_superuser = bool(getattr(effective_user, "is_superuser", False))

        available_tabs = [dashboard_label, people_label, bookings_label, floorplan_label, pages_label]
        if can_create:
            available_tabs.append(new_request_label)
        if can_audit:
            available_tabs.append(audit_label)
        if is_admin:
            available_tabs.append(settings_label)
            available_tabs.append(ad_accounts_label)
        if is_superuser:
            available_tabs.append(users_label)
        tab_keys = {
            "dashboard": dashboard_label,
            "people": people_label,
            "bookings": bookings_label,
            "floorplan": floorplan_label,
            "pages": pages_label,
            "new_request": new_request_label,
            "audit": audit_label,
            "settings": settings_label,
            "ad_accounts": ad_accounts_label,
            "users": users_label,
        }
        requested_tab = tab_keys.get(tab or "")
        saved_tab = app.storage.user.get("active_tab")
        if requested_tab in available_tabs:
            initial_tab = requested_tab
        elif saved_tab in available_tabs:
            initial_tab = saved_tab
        else:
            initial_tab = dashboard_label

        refreshers: dict[str, Callable[[], Awaitable[None]]] = {}
        refreshing: set[str] = set()

        ui.colors(primary="#0F52AC")
        with ui.header().classes("row items-center justify-between px-4").style(
            "background-color: #0F52AC"
        ):
            ui.label(t("app_name")).classes("text-h6 text-white text-weight-light")
            with ui.tabs().classes("ml-4") as tabs:
                dashboard_tab = ui.tab(dashboard_label, icon="dashboard")
                ui.tab(people_label, icon="people")
                ui.tab(bookings_label, icon="event_available")
                ui.tab(floorplan_label, icon="map")
                ui.tab(pages_label, icon="article")
                if can_create:
                    ui.tab(new_request_label, icon="add_circle")
                if can_audit:
                    ui.tab(audit_label, icon="policy")
                if is_admin:
                    ui.tab(settings_label, icon="settings")
                    ui.tab(ad_accounts_label, icon="manage_accounts")
                if is_superuser:
                    ui.tab(users_label, icon="manage_accounts")

            async def on_tab_change(e):
                app.storage.user["active_tab"] = e.value
                refresh = refreshers.get(e.value)
                if refresh is None or e.value in refreshing:
                    return
                refreshing.add(e.value)
                try:
                    await refresh()
                finally:
                    refreshing.discard(e.value)

            tabs.on_value_change(on_tab_change)

            with ui.row().classes("items-center"):
                def on_lang_change(e):
                    set_locale(e.value)
                    ui.run_javascript("window.location.reload()")

                ui.toggle(
                    list(SUPPORTED_LOCALES), value=locale, on_change=on_lang_change
                ).props("flat dense color=white text-color=white toggle-color=white")

                if logged_in:
                    with ui.button(icon="person").props("flat color=white"):
                        with ui.menu():
                            ui.menu_item(t("my_profile"), on_click=lambda: tabs.set_value(people_label))
                            ui.menu_item(t("logout"), on_click=lambda: _logout())
                else:
                    ui.button(t("log_in"), icon="login", on_click=lambda: ui.navigate.to("/login")).props(
                        "flat color=white"
                    )

        # Tab content is rendered once at page load; switching to a tab
        # re-runs its refresh so the data is current. Form/edit surfaces
        # (new request, settings, AD accounts) are excluded — refreshing
        # them would discard in-progress input.
        with ui.tab_panels(tabs, value=initial_tab).classes("w-full"):
            with ui.tab_panel(dashboard_label):
                refreshers[dashboard_label] = render_dashboard(effective_user)
            with ui.tab_panel(people_label):
                refreshers[people_label] = render_directory(effective_user)
            with ui.tab_panel(bookings_label):
                refreshers[bookings_label] = render_bookings(effective_user)
            with ui.tab_panel(floorplan_label):
                refreshers[floorplan_label] = render_floorplan(effective_user)
            with ui.tab_panel(pages_label):
                refreshers[pages_label] = render_pages(effective_user)
            if can_create:
                with ui.tab_panel(new_request_label):
                    await render_new_request(effective_user)
            if can_audit:
                with ui.tab_panel(audit_label):
                    refreshers[audit_label] = render_audit()
            if is_admin:
                with ui.tab_panel(settings_label):
                    await render_settings(effective_user)
                with ui.tab_panel(ad_accounts_label):
                    await render_ad_accounts(effective_user)
            if is_superuser:
                with ui.tab_panel(users_label):
                    refreshers[users_label] = await render_user_management(effective_user)

        if logged_in:
            from not_dot_net.backend.workflow_service import get_actionable_count

            last_count: int | None = None

            async def update_badge():
                nonlocal last_count
                try:
                    count = await _safe_actionable_count(effective_user)
                    tab_text = f"{dashboard_label} ({count})" if count > 0 else dashboard_label
                    dashboard_tab._props["label"] = tab_text
                    dashboard_tab.update()
                    changed = last_count is not None and count != last_count
                    last_count = count
                    if changed and tabs.value == dashboard_label:
                        await refreshers[dashboard_label]()
                    title = f"({count}) NotDotNet" if count > 0 else "NotDotNet"
                    await ui.run_javascript(f"document.title = {title!r}")
                except (RuntimeError, TimeoutError):
                    # Client vanished mid-update (disconnect, reload) — expected.
                    pass

            ui.timer(60, update_badge)
            ui.timer(0, update_badge, once=True)

        return None


def _logout():
    ui.navigate.to("/logout")
