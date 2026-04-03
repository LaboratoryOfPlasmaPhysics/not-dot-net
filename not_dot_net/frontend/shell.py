from typing import Optional

from fastapi import Depends
from fastapi.responses import RedirectResponse
from nicegui import app, ui

from not_dot_net.backend.db import User
from not_dot_net.backend.roles import Role, has_role
from not_dot_net.backend.users import current_active_user_optional
from not_dot_net.frontend.admin_settings import render as render_settings
from not_dot_net.frontend.audit_log import render as render_audit
from not_dot_net.frontend.bookings import render as render_bookings
from not_dot_net.frontend.directory import render as render_directory
from not_dot_net.frontend.dashboard import render as render_dashboard
from not_dot_net.frontend.new_request import render as render_new_request
from not_dot_net.frontend.i18n import SUPPORTED_LOCALES, get_locale, set_locale, t


def setup():
    @ui.page("/")
    async def main_page(
        user: Optional[User] = Depends(current_active_user_optional),
    ) -> Optional[RedirectResponse]:
        if not user:
            return RedirectResponse("/login")

        locale = get_locale()
        people_label = t("people")
        dashboard_label = t("dashboard")
        new_request_label = t("new_request")
        bookings_label = t("bookings")
        audit_label = t("audit_log")
        settings_label = t("settings")

        can_create = has_role(user, Role.STAFF)
        is_admin = has_role(user, Role.ADMIN)

        # Restore last active tab (fall back to dashboard)
        available_tabs = [dashboard_label, people_label, bookings_label]
        if can_create:
            available_tabs.append(new_request_label)
        if is_admin:
            available_tabs.append(audit_label)
            available_tabs.append(settings_label)
        saved_tab = app.storage.user.get("active_tab")
        initial_tab = saved_tab if saved_tab in available_tabs else dashboard_label

        ui.colors(primary="#0F52AC")
        with ui.header().classes("row items-center justify-between px-4").style(
            "background-color: #0F52AC"
        ):
            ui.label(t("app_name")).classes("text-h6 text-white text-weight-light")
            with ui.tabs().classes("ml-4") as tabs:
                ui.tab(dashboard_label, icon="dashboard")
                ui.tab(people_label, icon="people")
                ui.tab(bookings_label, icon="event_available")
                if can_create:
                    ui.tab(new_request_label, icon="add_circle")
                if is_admin:
                    ui.tab(audit_label, icon="policy")
                    ui.tab(settings_label, icon="settings")

            def on_tab_change(e):
                app.storage.user["active_tab"] = e.value

            tabs.on_value_change(on_tab_change)

            with ui.row().classes("items-center"):
                def on_lang_change(e):
                    set_locale(e.value)
                    ui.run_javascript("window.location.reload()")

                ui.toggle(
                    list(SUPPORTED_LOCALES), value=locale, on_change=on_lang_change
                ).props("flat dense color=white text-color=white toggle-color=white")

                with ui.button(icon="person").props("flat color=white"):
                    with ui.menu():
                        ui.menu_item(t("my_profile"), on_click=lambda: tabs.set_value(people_label))
                        ui.menu_item(t("logout"), on_click=lambda: _logout())

        with ui.tab_panels(tabs, value=initial_tab).classes("w-full"):
            with ui.tab_panel(dashboard_label):
                render_dashboard(user)
            with ui.tab_panel(people_label):
                render_directory(user)
            with ui.tab_panel(bookings_label):
                render_bookings(user)
            if can_create:
                with ui.tab_panel(new_request_label):
                    await render_new_request(user)
            if is_admin:
                with ui.tab_panel(audit_label):
                    render_audit()
                with ui.tab_panel(settings_label):
                    await render_settings(user)

        return None


def _logout():
    app.storage.user["authenticated"] = False
    ui.run_javascript(
        "document.cookie = 'fastapiusersauth=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT';"
        "window.location.href = '/login';"
    )
