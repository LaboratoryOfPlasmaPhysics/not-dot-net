import importlib
from types import SimpleNamespace

from starlette.datastructures import Headers

from nicegui.testing import User
from not_dot_net.frontend.login import handle_logout


async def test_login_page_renders(user: User) -> None:
    await user.open("/login")
    await user.should_see("Log in")


async def test_main_page_redirects_when_unauthenticated(user: User) -> None:
    await user.open("/")
    await user.should_see("Log in")


async def test_login_page_shows_app_title(user: User) -> None:
    await user.open("/login")
    await user.should_see("LPP Intranet")


def test_auth_cookie_not_marked_secure_in_dev() -> None:
    from not_dot_net.backend.users import cookie_transport

    assert cookie_transport.cookie_secure is False


def test_auth_cookie_marked_secure_when_database_url_is_set(monkeypatch) -> None:
    import not_dot_net.backend.users as users_module

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@db/app")
    reloaded = importlib.reload(users_module)
    try:
        assert reloaded.cookie_transport.cookie_secure is True
    finally:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        importlib.reload(users_module)


async def test_logout_redirects_to_login_and_clears_auth_cookie() -> None:
    request = SimpleNamespace(headers=Headers({"sec-fetch-site": "same-origin"}))
    response = await handle_logout(request=request, user=None)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    set_cookie_headers = response.headers.getlist("set-cookie")
    assert any("fastapiusersauth=" in header for header in set_cookie_headers)
    assert any("Max-Age=0" in header for header in set_cookie_headers)


async def test_login_page_redirects_authenticated_users(user: User) -> None:
    """R-06: a logged-in user visiting /login must land on the app, not the
    login form. (The old check read a storage flag that nothing ever set.)"""
    import asyncio
    from contextlib import asynccontextmanager

    from not_dot_net.backend.db import session_scope, get_user_db
    from not_dot_net.backend.schemas import UserCreate
    from not_dot_net.backend.users import get_user_manager, get_jwt_strategy

    await user.open("/login")
    await asyncio.sleep(0.2)  # let startup tasks finish

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                from fastapi_users.exceptions import UserAlreadyExists
                try:
                    db_user = await manager.create(
                        UserCreate(email="relogin@test.com", password="pw123456")
                    )
                except UserAlreadyExists:
                    db_user = await manager.get_by_email("relogin@test.com")
    token = await get_jwt_strategy().write_token(db_user)
    user.http_client.cookies.set("fastapiusersauth", token)

    await user.open("/login")
    await user.should_see("Dashboard")


def test_session_lifetime_covers_a_workday():
    """R-14: 1h cookie+JWT expiry silently logged people out mid-morning."""
    from not_dot_net.backend.users import cookie_transport, get_jwt_strategy

    assert cookie_transport.cookie_max_age >= 12 * 3600
    assert get_jwt_strategy().lifetime_seconds == cookie_transport.cookie_max_age
