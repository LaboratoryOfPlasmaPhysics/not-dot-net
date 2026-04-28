"""Tests for user update and delete operations (directory functions)."""

import pytest
import uuid
from contextlib import asynccontextmanager
from datetime import date

from not_dot_net.backend.db import User, session_scope, get_user_db
from not_dot_net.backend.users import get_user_manager
from not_dot_net.backend.schemas import UserCreate, UserUpdate


async def _create_user(email="user@test.com", password="Password1!") -> User:
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                return await manager.create(UserCreate(email=email, password=password))


async def _update_user(user_id, updates: dict):
    """Mirror of directory.py _update_user."""
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                user = await manager.get(user_id)
                update_schema = UserUpdate(**updates)
                await manager.update(update_schema, user)


async def _delete_user(user_id):
    """Mirror of directory.py _delete_user."""
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                user = await manager.get(user_id)
                await manager.delete(user)


async def test_update_user_full_name():
    user = await _create_user()
    await _update_user(user.id, {"full_name": "Alice Smith"})
    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.full_name == "Alice Smith"


async def test_update_user_multiple_fields():
    user = await _create_user()
    await _update_user(user.id, {"full_name": "Bob", "phone": "+33123", "office": "B204"})
    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.full_name == "Bob"
        assert refreshed.phone == "+33123"
        assert refreshed.office == "B204"


async def test_update_user_extended_profile_fields():
    user = await _create_user()
    await _update_user(user.id, {
        "team": "Plasma",
        "title": "Engineer",
        "employment_status": "CDD",
        "company": "CNRS",
        "description": "Works on diagnostics",
        "webpage": "https://example.test/alice",
    })
    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.team == "Plasma"
        assert refreshed.title == "Engineer"
        assert refreshed.employment_status == "CDD"
        assert refreshed.company == "CNRS"
        assert refreshed.description == "Works on diagnostics"
        assert refreshed.webpage == "https://example.test/alice"


async def test_update_user_dates():
    user = await _create_user()
    await _update_user(user.id, {
        "start_date": date(2026, 3, 1),
        "end_date": date(2026, 8, 31),
    })
    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.start_date == date(2026, 3, 1)
        assert refreshed.end_date == date(2026, 8, 31)


async def test_update_user_can_clear_optional_fields():
    user = await _create_user()
    await _update_user(user.id, {
        "full_name": "Temporary Name",
        "phone": "+33123",
        "office": "B204",
        "webpage": "https://example.test",
    })

    await _update_user(user.id, {
        "full_name": None,
        "phone": None,
        "office": None,
        "webpage": None,
    })

    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.full_name is None
        assert refreshed.phone is None
        assert refreshed.office is None
        assert refreshed.webpage is None


async def test_update_user_email():
    user = await _create_user(email="old-email@test.com")
    await _update_user(user.id, {"email": "new-email@test.com"})
    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.email == "new-email@test.com"


async def test_update_user_active_flag():
    user = await _create_user()
    await _update_user(user.id, {"is_active": False})
    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.is_active is False


async def test_update_user_role():
    user = await _create_user()
    await _update_user(user.id, {"role": "admin"})
    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.role == "admin"
        assert refreshed.is_superuser is True


async def test_update_user_role_demotion_clears_superuser():
    user = await _create_user()
    await _update_user(user.id, {"role": "admin"})
    await _update_user(user.id, {"role": "member"})
    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.role == "member"
        assert refreshed.is_superuser is False


async def test_delete_user():
    user = await _create_user()
    await _delete_user(user.id)
    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        # FastAPI-Users soft-deletes by setting is_active=False
        # or hard-deletes depending on config. Check either case.
        if refreshed is not None:
            assert refreshed.is_active is False
        else:
            assert refreshed is None


async def test_delete_nonexistent_user_raises():
    from fastapi_users.exceptions import UserNotExists
    with pytest.raises(UserNotExists):
        await _delete_user(uuid.uuid4())
