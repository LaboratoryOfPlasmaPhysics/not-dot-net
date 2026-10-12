"""Integration tests for local auth endpoints (login)."""

import asyncio
import pytest
from contextlib import asynccontextmanager
from types import SimpleNamespace

from starlette.datastructures import Headers
from unittest.mock import AsyncMock, patch

from not_dot_net.backend.db import User, session_scope, get_user_db
from not_dot_net.backend.audit import list_audit_events
from not_dot_net.backend.users import get_user_manager
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.frontend.login import handle_login

async def _create_user_via_manager(email="test@test.com", password="Secret123!"):
    """Create a user through the UserManager."""
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                return await manager.create(UserCreate(email=email, password=password))


async def _authenticate_via_manager(email, password):
    """Authenticate a user through the UserManager (same path as the login endpoint)."""
    from fastapi.security import OAuth2PasswordRequestForm
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                form = OAuth2PasswordRequestForm(
                    username=email, password=password,
                    scope="", grant_type="password",
                )
                return await manager.authenticate(form)


class _FakeRequest:
    def __init__(self, form_data, *, client_host=None, headers=None):
        self._form_data = form_data
        self.client = SimpleNamespace(host=client_host) if client_host else None
        self.headers = Headers(headers or {})

    async def form(self):
        return self._form_data


async def test_register_creates_user():
    user = await _create_user_via_manager("new@test.com", "Password1!")
    assert user is not None
    assert user.email == "new@test.com"
    assert user.is_active is True


async def test_register_duplicate_raises():
    from fastapi_users.exceptions import UserAlreadyExists
    await _create_user_via_manager("dup@test.com", "Password1!")
    with pytest.raises(UserAlreadyExists):
        await _create_user_via_manager("dup@test.com", "Password1!")


async def test_authenticate_valid_credentials():
    await _create_user_via_manager("auth@test.com", "CorrectPassword1!")
    user = await _authenticate_via_manager("auth@test.com", "CorrectPassword1!")
    assert user is not None
    assert user.email == "auth@test.com"


async def test_authenticate_wrong_password():
    await _create_user_via_manager("auth2@test.com", "CorrectPassword1!")
    user = await _authenticate_via_manager("auth2@test.com", "WrongPassword!")
    assert user is None


async def test_authenticate_nonexistent_user():
    user = await _authenticate_via_manager("nobody@test.com", "Whatever1!")
    assert user is None


async def test_jwt_token_generation():
    from not_dot_net.backend.users import get_jwt_strategy
    user = await _create_user_via_manager("jwt@test.com", "Password1!")
    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    assert isinstance(token, str)
    assert len(token) > 20


async def test_login_endpoint_success_sets_auth_cookie_and_redirects():
    await _create_user_via_manager("success@test.com", "CorrectPassword1!")

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                response = await handle_login(
                    _FakeRequest(
                        {"username": "success@test.com", "password": "CorrectPassword1!"}
                    ),
                    user_manager=user_manager,
                )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    set_cookie_headers = response.headers.getlist("set-cookie")
    assert any("fastapiusersauth=" in header for header in set_cookie_headers)


async def test_login_endpoint_success_honors_safe_redirect():
    await _create_user_via_manager("redirect@test.com", "CorrectPassword1!")

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                response = await handle_login(
                    _FakeRequest(
                        {
                            "username": "redirect@test.com",
                            "password": "CorrectPassword1!",
                            "redirect_to": "/dashboard?tab=security",
                        }
                    ),
                    user_manager=user_manager,
                )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard?tab=security"


@pytest.mark.parametrize(
    "unsafe_redirect",
    [
        "https://evil.example/steal",
        "//evil.example/steal",
        "javascript:alert(1)",
        "data:text/html,owned",
        "evil.example/no-leading-slash",
        "/\\evil.example",
    ],
)
async def test_login_endpoint_success_rejects_unsafe_redirect(unsafe_redirect: str):
    await _create_user_via_manager("redirect-unsafe@test.com", "CorrectPassword1!")

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                response = await handle_login(
                    _FakeRequest(
                        {
                            "username": "redirect-unsafe@test.com",
                            "password": "CorrectPassword1!",
                            "redirect_to": unsafe_redirect,
                        }
                    ),
                    user_manager=user_manager,
                )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


async def test_login_endpoint_does_not_enumerate_accounts():
    await _create_user_via_manager("enum@test.com", "CorrectPassword1!")

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                wrong_password = await handle_login(
                    _FakeRequest(
                        {"username": "enum@test.com", "password": "WrongPassword!"}
                    ),
                    user_manager=user_manager,
                )
                nonexistent_user = await handle_login(
                    _FakeRequest(
                        {"username": "missing@test.com", "password": "WrongPassword!"}
                    ),
                    user_manager=user_manager,
                )

    assert wrong_password.status_code == 303
    assert nonexistent_user.status_code == 303
    assert wrong_password.headers["location"] == "/login?error=1"
    assert nonexistent_user.headers["location"] == "/login?error=1"
    assert "set-cookie" not in wrong_password.headers
    assert "set-cookie" not in nonexistent_user.headers


async def test_login_endpoint_empty_credentials_fail_like_any_other_failure():
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                response = await handle_login(
                    _FakeRequest({"username": "", "password": ""}),
                    user_manager=user_manager,
                )

    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=1"
    assert "set-cookie" not in response.headers


async def test_login_endpoint_rejects_inactive_user():
    user = await _create_user_via_manager("inactive@test.com", "CorrectPassword1!")
    async with session_scope() as session:
        db_user = await session.get(User, user.id)
        assert db_user is not None
        db_user.is_active = False
        await session.commit()

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                response = await handle_login(
                    _FakeRequest(
                        {"username": "inactive@test.com", "password": "CorrectPassword1!"}
                    ),
                    user_manager=user_manager,
                )

    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=1"
    assert "set-cookie" not in response.headers


async def test_login_endpoint_success_emits_audit_event():
    await _create_user_via_manager("audit-success@test.com", "CorrectPassword1!")

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                response = await handle_login(
                    _FakeRequest(
                        {"username": "audit-success@test.com", "password": "CorrectPassword1!"}
                    ),
                    user_manager=user_manager,
                )

    assert response.status_code == 303
    events = await list_audit_events(category="auth", action="login")
    assert len(events) == 1
    assert events[0].actor_email == "audit-success@test.com"


async def test_superuser_successful_login_emits_audit_with_ip_role_metadata():
    user = await _create_user_via_manager("admin-success@test.com", "CorrectPassword1!")
    async with session_scope() as session:
        db_user = await session.get(User, user.id)
        assert db_user is not None
        db_user.is_superuser = True
        db_user.role = "security_admin"
        await session.commit()

    with patch(
        "not_dot_net.backend.security_alerts.notify_superuser_login_success",
        new=AsyncMock(return_value=["root@test.com"]),
    ) as notify_mock:
        async with session_scope() as session:
            async with asynccontextmanager(get_user_db)(session) as user_db:
                async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                    response = await handle_login(
                        _FakeRequest(
                            {
                                "username": "admin-success@test.com",
                                "password": "CorrectPassword1!",
                            },
                            client_host="10.0.0.24",
                            headers={"user-agent": "pytest-browser"},
                        ),
                        user_manager=user_manager,
                    )

    assert response.status_code == 303
    await asyncio.sleep(0)
    notify_mock.assert_awaited_once()
    args, kwargs = notify_mock.await_args
    assert args[0].email == "admin-success@test.com"
    assert kwargs == {"ip": "10.0.0.24", "user_agent": "pytest-browser"}
    events = await list_audit_events(category="auth", action="login")
    assert len(events) == 1
    assert events[0].actor_email == "admin-success@test.com"
    assert events[0].detail == "Login Success ip=10.0.0.24 role=security_admin is_superuser=True"
    assert events[0].metadata_json == {
        "ip": "10.0.0.24",
        "user_agent": "pytest-browser",
        "network": {
            "client_host": "10.0.0.24",
            "x_forwarded_for": None,
            "x_real_ip": None,
            "forwarded": None,
            "user_agent": "pytest-browser",
        },
        "role": "security_admin",
        "is_superuser": True,
        "success": True,
    }


async def test_login_endpoint_failed_login_does_not_emit_success_audit_event():
    await _create_user_via_manager("audit-failure@test.com", "CorrectPassword1!")

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                response = await handle_login(
                    _FakeRequest(
                        {"username": "audit-failure@test.com", "password": "WrongPassword!"}
                    ),
                    user_manager=user_manager,
                )

    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=1"
    assert await list_audit_events(category="auth", action="login") == []


async def test_superuser_failed_login_emits_audit_with_ip_metadata():
    user = await _create_user_via_manager("admin-fail@test.com", "CorrectPassword1!")
    async with session_scope() as session:
        db_user = await session.get(User, user.id)
        assert db_user is not None
        db_user.is_superuser = True
        await session.commit()

    with patch(
        "not_dot_net.backend.security_alerts.notify_superuser_login_failed",
        new=AsyncMock(return_value=["root@test.com"]),
    ) as notify_mock:
        async with session_scope() as session:
            async with asynccontextmanager(get_user_db)(session) as user_db:
                async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                    response = await handle_login(
                        _FakeRequest(
                            {"username": "admin-fail@test.com", "password": "WrongPassword!"},
                            client_host="10.0.0.42",
                            headers={"user-agent": "pytest-browser"},
                        ),
                        user_manager=user_manager,
                    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=1"
    await asyncio.sleep(0)
    notify_mock.assert_awaited_once()
    args, kwargs = notify_mock.await_args
    assert args[0].email == "admin-fail@test.com"
    assert kwargs == {"ip": "10.0.0.42", "user_agent": "pytest-browser"}
    events = await list_audit_events(category="auth", action="login")
    assert len(events) == 1
    assert events[0].actor_email == "admin-fail@test.com"
    assert events[0].detail == "Login Failed ip=10.0.0.42"
    assert events[0].metadata_json == {
        "ip": "10.0.0.42",
        "user_agent": "pytest-browser",
        "network": {
            "client_host": "10.0.0.42",
            "x_forwarded_for": None,
            "x_real_ip": None,
            "forwarded": None,
            "user_agent": "pytest-browser",
        },
        "is_superuser": True,
        "success": False,
    }


async def test_login_endpoint_falls_back_to_ldap_when_local_auth_fails(monkeypatch):
    ldap_user = User(
        email="ldap-success@test.com",
        hashed_password="x",
        is_active=True,
    )

    async def fake_try_ldap_auth(username: str, password: str):
        if username == "ldap-success@test.com" and password == "CorrectPassword1!":
            return ldap_user
        return None

    monkeypatch.setattr("not_dot_net.frontend.login._try_ldap_auth", fake_try_ldap_auth)

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                response = await handle_login(
                    _FakeRequest(
                        {"username": "ldap-success@test.com", "password": "CorrectPassword1!"}
                    ),
                    user_manager=user_manager,
                )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    set_cookie_headers = response.headers.getlist("set-cookie")
    assert any("fastapiusersauth=" in header for header in set_cookie_headers)
