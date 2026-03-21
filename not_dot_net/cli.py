import asyncio
from typing import Optional
from contextlib import asynccontextmanager

from cyclopts import App
from yaml import safe_dump

app = App(name="NotDotNet", version="0.1.0")


@app.command
def serve(
    host: str = "localhost",
    port: int = 8088,
    env_file: Optional[str] = None,
    seed_fake_users: bool = False,
):
    """Serve the NotDotNet application."""
    from not_dot_net.app import main

    main(host, port, env_file, seed_fake_users=seed_fake_users)


@app.command
def create_user(username: str, password: str, env_file: Optional[str] = None):
    """Create a new user."""

    async def _create():
        from not_dot_net.config import init_settings
        from not_dot_net.backend.db import init_db, create_db_and_tables, get_async_session, get_user_db
        from not_dot_net.backend.users import get_user_manager
        from not_dot_net.backend.schemas import UserCreate

        settings = init_settings(env_file)
        init_db(settings.backend.database_url)
        await create_db_and_tables()

        async with asynccontextmanager(get_async_session)() as session:
            async with asynccontextmanager(get_user_db)(session) as user_db:
                async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                    user = await user_manager.create(
                        UserCreate(
                            email=username,
                            password=password,
                            is_active=True,
                            is_superuser=False,
                        )
                    )
                    print(f"User '{user.email}' created successfully.")

    asyncio.run(_create())


@app.command
def default_config():
    """Print default configuration as YAML."""
    from not_dot_net.config import Settings

    print(safe_dump(Settings().model_dump()))


if __name__ in {"__main__", "__mp_main__"}:
    app()
