from typing import Optional

from fastapi import Depends
from fastapi.responses import RedirectResponse
from nicegui import ui

from not_dot_net.backend.db import User
from not_dot_net.backend.users import current_active_user_optional
from not_dot_net.frontend.directory import render as render_directory


def setup():
    @ui.page("/")
    def main_page(
        user: Optional[User] = Depends(current_active_user_optional),
    ) -> Optional[RedirectResponse]:
        if not user:
            return RedirectResponse("/login")

        with ui.header().classes("row items-center justify-between px-4"):
            ui.label("LPP Intranet").classes("text-h6 text-white")
            with ui.tabs().classes("ml-4") as tabs:
                ui.tab("People", icon="people")
                ui.tab("Onboarding", icon="person_add")
            with ui.row().classes("items-center"):
                with ui.button(icon="person").props("flat color=white"):
                    with ui.menu():
                        ui.menu_item("My Profile", on_click=lambda: _go_to_profile(tabs))
                        ui.menu_item("Logout", on_click=lambda: _logout())

        with ui.tab_panels(tabs, value="People").classes("w-full"):
            with ui.tab_panel("People"):
                render_directory(user)
            with ui.tab_panel("Onboarding"):
                ui.label("Onboarding placeholder")

        return None


def _go_to_profile(tabs):
    tabs.set_value("People")


def _logout():
    ui.run_javascript(
        'document.cookie = "fastapiusersauth=; path=/; max-age=0";'
        'window.location.href = "/login";'
    )
