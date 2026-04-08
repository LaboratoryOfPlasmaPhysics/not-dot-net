import asyncio
from contextlib import asynccontextmanager

from nicegui.testing import User

from not_dot_net.backend.db import session_scope, get_user_db
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.backend.users import get_user_manager, get_jwt_strategy


async def _create_user_and_token(email: str, password: str) -> str:
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                from fastapi_users.exceptions import UserAlreadyExists
                try:
                    user = await manager.create(UserCreate(email=email, password=password))
                except UserAlreadyExists:
                    user = await manager.get_by_email(email)
                return await get_jwt_strategy().write_token(user)


async def test_directory_shows_search(user: User) -> None:
    await user.open("/login")
    # Wait for startup tasks (DB creation, admin seeding) to complete
    await asyncio.sleep(0.5)
    token = await _create_user_and_token("admin@not-dot-net.dev", "admin")
    user.http_client.cookies.set("fastapiusersauth", token)
    await user.open("/")
    await user.should_see("Search")
