import asyncio
import os
from contextlib import asynccontextmanager

from cyclopts import App

app = App(name="NotDotNet", version="0.1.0")


@app.command
def serve(
    host: str = "localhost",
    port: int = 8088,
    secrets_file: str = "./secrets.key",
    seed_fake_users: bool = False,
):
    """Serve the NotDotNet application."""
    from not_dot_net.app import main
    main(host, port, secrets_file, seed_fake_users=seed_fake_users)


@app.command
def create_user(
    username: str,
    password: str,
    role: str = "member",
    secrets_file: str = "./secrets.key",
):
    """Create a new user."""
    async def _create():
        from pathlib import Path
        from not_dot_net.backend.db import init_db, create_db_and_tables, session_scope, get_user_db
        from not_dot_net.backend.secrets import load_or_create
        from not_dot_net.backend.users import get_user_manager, init_user_secrets
        from not_dot_net.backend.schemas import UserCreate
        from not_dot_net.backend.roles import Role

        database_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")
        dev_mode = "DATABASE_URL" not in os.environ

        init_db(database_url)
        secrets = load_or_create(Path(secrets_file), dev_mode=dev_mode)
        init_user_secrets(secrets)
        await create_db_and_tables()

        user_role = Role(role)

        async with session_scope() as session:
            async with asynccontextmanager(get_user_db)(session) as user_db:
                async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                    user = await user_manager.create(
                        UserCreate(
                            email=username,
                            password=password,
                            is_active=True,
                            is_superuser=(user_role == Role.ADMIN),
                        )
                    )
                    user.role = user_role
                    session.add(user)
                    await session.commit()
                    print(f"User '{user.email}' created with role '{role}'.")

    asyncio.run(_create())


if __name__ in {"__main__", "__mp_main__"}:
    app()
