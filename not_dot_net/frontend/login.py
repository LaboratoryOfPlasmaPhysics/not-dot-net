from typing import Optional

from fastapi.responses import RedirectResponse
from nicegui import app, ui

from not_dot_net.backend.users import authenticate_and_get_token
from not_dot_net.frontend.i18n import t


def setup():
    @ui.page("/login")
    def login(redirect_to: str = "/") -> Optional[RedirectResponse]:
        if app.storage.user.get("authenticated", False):
            return RedirectResponse(redirect_to)

        async def try_login() -> None:
            try:
                token = await authenticate_and_get_token(email.value, password.value)
                if token is None:
                    ui.notify(t("invalid_credentials"), color="negative")
                    return

                ui.run_javascript(
                    f'document.cookie = "fastapiusersauth={token}; path=/; SameSite=Lax";'
                    f'window.location.href = "{redirect_to}";'
                )
            except Exception:
                ui.notify(t("auth_error"), color="negative")

        with ui.column().classes("absolute-center items-center gap-4"):
            ui.label(t("app_name")).classes("text-h4 text-weight-light")
            with ui.card().classes("w-80"):
                email = ui.input(t("email")).props("outlined dense").classes(
                    "w-full"
                ).on("keydown.enter", try_login)
                password = ui.input(
                    t("password"), password=True, password_toggle_button=True
                ).props("outlined dense").classes("w-full").on(
                    "keydown.enter", try_login
                )
                ui.button(t("log_in"), on_click=try_login).classes("w-full")
        return None
