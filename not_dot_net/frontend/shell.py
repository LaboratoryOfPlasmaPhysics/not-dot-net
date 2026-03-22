from typing import Optional

from fastapi import Depends
from fastapi.responses import RedirectResponse
from nicegui import ui

from not_dot_net.backend.db import User
from not_dot_net.backend.users import current_active_user_optional
from not_dot_net.frontend.directory import render as render_directory
from not_dot_net.frontend.i18n import SUPPORTED_LOCALES, get_locale, set_locale, t


def setup():
    @ui.page("/")
    def main_page(
        user: Optional[User] = Depends(current_active_user_optional),
    ) -> Optional[RedirectResponse]:
        if not user:
            return RedirectResponse("/login")

        locale = get_locale()
        people_label = t("people")

        with ui.header().classes("row items-center justify-between px-4"):
            ui.label(t("app_name")).classes("text-h6 text-white")
            with ui.tabs().classes("ml-4") as tabs:
                ui.tab(people_label, icon="people")
            with ui.row().classes("items-center"):
                def on_lang_change(e):
                    set_locale(e.value)
                    ui.run_javascript("window.location.reload()")

                ui.toggle(
                    list(SUPPORTED_LOCALES), value=locale, on_change=on_lang_change
                ).props("flat dense color=white text-color=white toggle-color=white")

                with ui.button(icon="person").props("flat color=white"):
                    with ui.menu():
                        ui.menu_item(t("my_profile"), on_click=lambda: _go_to_profile(tabs))
                        ui.menu_item(t("logout"), on_click=lambda: _logout())

        with ui.tab_panels(tabs, value=people_label).classes("w-full"):
            with ui.tab_panel(people_label):
                render_directory(user)

        return None


def _go_to_profile(tabs):
    tabs.set_value(t("people"))


def _logout():
    ui.run_javascript(
        'document.cookie = "fastapiusersauth=; path=/; max-age=0";'
        'window.location.href = "/login";'
    )
