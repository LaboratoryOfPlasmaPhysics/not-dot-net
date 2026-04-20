import importlib

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
    response = await handle_logout()

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    set_cookie_headers = response.headers.getlist("set-cookie")
    assert any("fastapiusersauth=" in header for header in set_cookie_headers)
    assert any("Max-Age=0" in header for header in set_cookie_headers)
