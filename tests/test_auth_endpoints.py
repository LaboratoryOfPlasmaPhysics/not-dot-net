"""Integration tests for local auth endpoints (login)."""

import pytest
import uuid
from contextlib import asynccontextmanager

from not_dot_net.backend.db import User, session_scope, get_user_db
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
    def __init__(self, form_data):
        self._form_data = form_data

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
