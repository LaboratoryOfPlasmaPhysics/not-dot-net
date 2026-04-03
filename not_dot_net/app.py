import logging
import os
from pathlib import Path

from nicegui import app, ui

from not_dot_net.backend.db import init_db, create_db_and_tables
from not_dot_net.backend.secrets import load_or_create
from not_dot_net.backend.users import (
    fastapi_users,
    jwt_backend,
    cookie_backend,
    init_user_secrets,
    ensure_default_admin,
)
from not_dot_net.backend.schemas import UserRead, UserUpdate
from not_dot_net.backend.auth import router as auth_router
from not_dot_net.frontend.login import setup as setup_login
from not_dot_net.frontend.shell import setup as setup_shell
from not_dot_net.frontend.workflow_token import setup as setup_token


DEV_DB_URL = "sqlite+aiosqlite:///./dev.db"
DEV_ADMIN_EMAIL = "admin@not-dot-net.dev"
DEV_ADMIN_PASSWORD = "admin"


def create_app(
    secrets_file: str = "./secrets.key",
    _seed_fake_users: bool = False,
):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    database_url = os.environ.get("DATABASE_URL", DEV_DB_URL)
    dev_mode = "DATABASE_URL" not in os.environ

    init_db(database_url)
    secrets = load_or_create(Path(secrets_file), dev_mode=dev_mode)
    init_user_secrets(secrets)

    async def startup():
        await create_db_and_tables()
        if dev_mode:
            await ensure_default_admin(DEV_ADMIN_EMAIL, DEV_ADMIN_PASSWORD)
        if _seed_fake_users:
            from not_dot_net.backend.seeding import seed_fake_users
            await seed_fake_users()

    app.on_startup(startup)

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

    from not_dot_net.frontend.i18n import validate_translations
    validate_translations()

    setup_login()
    setup_shell()
    setup_token()


def main(
    host: str = "localhost",
    port: int = 8088,
    secrets_file: str = "./secrets.key",
    seed_fake_users: bool = False,
) -> None:
    create_app(secrets_file, _seed_fake_users=seed_fake_users)
    from not_dot_net.backend.secrets import read_secrets_file
    secrets = read_secrets_file(Path(secrets_file))
    ui.run(
        storage_secret=secrets.storage_secret,
        host=host, port=port, reload=False, title="NotDotNet",
    )


# Used by NiceGUI test framework (runpy.run_path with __main__)
# and by _dev.py reload worker (runpy.run_path with __mp_main__)
if __name__ in {"__main__", "__mp_main__"}:
    main()
