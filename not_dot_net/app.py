import logging
import os
from pathlib import Path

from nicegui import app, ui

from not_dot_net.backend.db import init_db, create_db_and_tables
from not_dot_net.backend.migrate import run_upgrade
from not_dot_net.backend.secrets import load_or_create
from not_dot_net.backend.users import init_user_secrets, ensure_default_admin, set_dev_mode
import not_dot_net.backend.auth.ldap  # noqa: F401 — register LdapConfig section
from not_dot_net.frontend.login import setup as setup_login, login_router
from not_dot_net.frontend.shell import setup as setup_shell
from not_dot_net.frontend.workflow_token import setup as setup_token
from not_dot_net.frontend.workflow_detail import setup as setup_workflow_detail
from not_dot_net.frontend.setup_wizard import setup as setup_wizard
from not_dot_net.frontend.public_page import setup as setup_public_pages


DEV_DB_URL = "sqlite+aiosqlite:///./dev.db"
DEV_ADMIN_EMAIL = "admin@not-dot-net.dev"
DEV_ADMIN_PASSWORD = "admin"

logger = logging.getLogger("not_dot_net.app")


def _lock_socketio_cors():
    """Restrict NiceGUI's Socket.IO CORS to same-origin only.

    NiceGUI hardcodes cors_allowed_origins='*' for its On Air feature.
    We don't use On Air, so lock it down to reject cross-origin WebSocket
    upgrades and XHR polling from foreign origins.
    """
    from nicegui import core
    allowed = os.environ.get("ALLOWED_ORIGINS", "").split(",")
    allowed = [o.strip() for o in allowed if o.strip()]
    if not allowed:
        allowed = []
    core.sio.eio.cors_allowed_origins = allowed
    logger.info("Socket.IO CORS locked to: %s", allowed or "(same-origin only)")


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

    logger.info("Starting NotDotNet (dev_mode=%s)", dev_mode)
    logger.info("Database: %s", database_url.split("@")[-1] if "@" in database_url else database_url)
    logger.info("Secrets file: %s", secrets_file)

    init_db(database_url)
    logger.info("Database engine initialized")

    try:
        secrets = load_or_create(Path(secrets_file), dev_mode=dev_mode)
    except (FileNotFoundError, RuntimeError) as exc:
        logger.error("Cannot load secrets: %s", exc)
        raise SystemExit(1) from exc
    init_user_secrets(secrets)
    set_dev_mode(dev_mode)
    logger.info("Secrets loaded (dev_mode=%s)", dev_mode)

    if not dev_mode:
        logger.info("Running migrations...")
        run_upgrade(database_url)
        logger.info("Migrations complete")
        _lock_socketio_cors()

    async def startup():
        logger.info("Running async startup...")
        if dev_mode:
            await create_db_and_tables()
            logger.info("Dev tables created")
        from not_dot_net.backend.roles import seed_admin_permissions
        await seed_admin_permissions()
        logger.info("Admin permissions seeded")
        if dev_mode:
            await ensure_default_admin(DEV_ADMIN_EMAIL, DEV_ADMIN_PASSWORD)
        if _seed_fake_users:
            from not_dot_net.backend.seeding import seed_fake_users
            await seed_fake_users()
        from not_dot_net.backend.auth.ldap import start_connection_reaper
        start_connection_reaper()
        logger.info("Startup complete")

    async def shutdown():
        logger.info("Shutting down...")
        from not_dot_net.backend.auth.ldap import drop_all_connections
        drop_all_connections()
        logger.info("Shutdown complete")

    app.on_startup(startup)
    app.on_shutdown(shutdown)

    app.include_router(login_router)

    from not_dot_net.frontend.i18n import validate_translations
    validate_translations()

    setup_login()
    setup_shell()
    setup_token()
    setup_workflow_detail()

    if not dev_mode:
        setup_wizard()

    setup_public_pages()


def main(
    host: str = "localhost",
    port: int = 8088,
    secrets_file: str = "./secrets.key",
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
    seed_fake_users: bool = False,
) -> None:
    create_app(secrets_file, _seed_fake_users=seed_fake_users)
    from not_dot_net.backend.secrets import read_secrets_file
    secrets = read_secrets_file(Path(secrets_file))
    ssl_kwargs = {}
    if ssl_certfile and ssl_keyfile:
        ssl_kwargs = {"ssl_certfile": ssl_certfile, "ssl_keyfile": ssl_keyfile}
    ui.run(
        storage_secret=secrets.storage_secret,
        host=host, port=port, reload=False, title="NotDotNet",
        **ssl_kwargs,
    )


# Used by NiceGUI test framework (runpy.run_path with __main__)
# and by _dev.py reload worker (runpy.run_path with __mp_main__)
if __name__ in {"__main__", "__mp_main__"}:
    main()
