import pytest
from not_dot_net.backend.db import User, session_scope
from sqlalchemy import select


async def test_has_admin_returns_false_when_no_users():
    from not_dot_net.frontend.setup_wizard import has_admin
    assert await has_admin() is False


async def test_has_admin_returns_false_when_only_non_admin_users_exist():
    from not_dot_net.frontend.setup_wizard import has_admin

    async with session_scope() as session:
        session.add(User(email="member@test.dev", hashed_password="x", role="member"))
        session.add(User(email="staff@test.dev", hashed_password="x", role="staff"))
        await session.commit()

    assert await has_admin() is False


async def test_has_admin_returns_true_after_admin_created():
    from not_dot_net.frontend.setup_wizard import has_admin
    from not_dot_net.backend.users import ensure_default_admin
    await ensure_default_admin("admin@test.dev", "password")
    assert await has_admin() is True


async def test_has_admin_returns_true_when_at_least_one_admin_exists_among_other_users():
    from not_dot_net.frontend.setup_wizard import has_admin

    async with session_scope() as session:
        session.add(User(email="member@test.dev", hashed_password="x", role="member"))
        session.add(User(email="admin@test.dev", hashed_password="x", role="admin"))
        session.add(User(email="admin2@test.dev", hashed_password="x", role="admin"))
        await session.commit()

    assert await has_admin() is True
