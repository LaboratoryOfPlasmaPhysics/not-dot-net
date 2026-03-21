from typing import Optional

from nicegui import app, ui

from not_dot_net.config import init_settings
from not_dot_net.backend.db import init_db, create_db_and_tables
from not_dot_net.backend.users import fastapi_users, jwt_backend, cookie_backend
from not_dot_net.backend.schemas import UserRead, UserUpdate
from not_dot_net.backend.auth import router as auth_router
from not_dot_net.frontend.login import setup as setup_login
from not_dot_net.frontend.user_page import setup as setup_user_page


def create_app(config_file: str | None = None):
    settings = init_settings(config_file)
    init_db(settings.backend.database_url)

    app.on_startup(create_db_and_tables)

    app.include_router(
        fastapi_users.get_auth_router(jwt_backend),
        prefix="/auth/jwt",
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_auth_router(cookie_backend),
        prefix="/auth/cookie",
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix="/users",
        tags=["users"],
    )
    app.include_router(auth_router)

    setup_login()
    setup_user_page()


@ui.page("/")
def main_page() -> None:
    with ui.header().classes(replace="row items-center") as header:
        ui.button(on_click=lambda: left_drawer.toggle(), icon="menu").props(
            "flat color=white"
        )
        with ui.tabs() as tabs:
            ui.tab("A")
            ui.tab("B")
            ui.tab("C")

    with ui.footer(value=False) as footer:
        ui.label("Footer")

    with ui.left_drawer().classes("bg-blue-100") as left_drawer:
        ui.label("Side menu")

    with ui.page_sticky(position="bottom-right", x_offset=20, y_offset=20):
        ui.button(on_click=footer.toggle, icon="contact_support").props("fab")

    with ui.tab_panels(tabs, value="A").classes("w-full"):
        with ui.tab_panel("A"):
            ui.label("Content of A")
        with ui.tab_panel("B"):
            ui.label("Content of B")
        with ui.tab_panel("C"):
            ui.label("Content of C")


def main(
    host: str = "localhost",
    port: int = 8000,
    env_file: Optional[str] = None,
    reload=False,
) -> None:
    create_app(env_file)
    from not_dot_net.config import get_settings
    ui.run(
        storage_secret=get_settings().storage_secret,
        host=host, port=port, reload=reload, title="NotDotNet",
    )


if __name__ in {"__main__", "__mp_main__"}:
    main("localhost", 8000, None)
