"""P5 — permission-based recipient lookup loaded every active user, then asked
has_permissions per user.

The config cache removed the per-user query, but the full-table scan remains —
and User.photo is a non-deferred LargeBinary, so every notification fan-out
dragged every profile photo through memory. Roles already map to permissions,
so the set of roles granting one is computable up front.
"""
import uuid

import pytest
from sqlalchemy import inspect as sa_inspect

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.roles import RoleDefinition, roles_config


async def _role(key: str, permissions: list[str]) -> None:
    cfg = await roles_config.get()
    cfg.roles[key] = RoleDefinition(label=key, permissions=permissions)
    await roles_config.set(cfg)


async def _user(email: str, **kwargs) -> User:
    async with session_scope() as session:
        kwargs.setdefault("role", "")
        user = User(email=email, hashed_password="x", is_active=True, **kwargs)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_returns_only_users_whose_role_grants_it():
    from not_dot_net.backend.permissions import users_with_permission

    await _role("approver", ["approve_workflows"])
    await _role("viewer", ["view_directory"])
    wanted = await _user("approver@example.com", role="approver")
    await _user("viewer@example.com", role="viewer")

    async with session_scope() as session:
        found = await users_with_permission(session, "approve_workflows")
    assert [u.email for u in found] == [wanted.email]


async def test_superusers_are_always_included():
    from not_dot_net.backend.permissions import users_with_permission

    await _role("nobody", [])
    boss = await _user("boss@example.com", role="nobody", is_superuser=True)

    async with session_scope() as session:
        found = await users_with_permission(session, "approve_workflows")
    assert boss.email in [u.email for u in found]


async def test_inactive_users_are_excluded():
    from not_dot_net.backend.permissions import users_with_permission

    await _role("approver2", ["approve_workflows"])
    async with session_scope() as session:
        session.add(User(
            email="gone@example.com", hashed_password="x",
            is_active=False, role="approver2",
        ))
        await session.commit()

    async with session_scope() as session:
        found = await users_with_permission(session, "approve_workflows")
    assert "gone@example.com" not in [u.email for u in found]


async def test_does_not_load_photo_bytes():
    from not_dot_net.backend.permissions import users_with_permission

    await _role("approver3", ["approve_workflows"])
    await _user("photo-heavy@example.com", role="approver3", photo=b"\xff\xd8\xff" + b"x" * 8192)

    async with session_scope() as session:
        found = await users_with_permission(session, "approve_workflows")
    assert found
    assert "photo" in sa_inspect(found[0]).unloaded, (
        "recipient lookup pulled the LargeBinary photo column"
    )


async def test_unknown_permission_returns_only_superusers():
    from not_dot_net.backend.permissions import users_with_permission

    await _role("plain", ["view_directory"])
    await _user("plain@example.com", role="plain")

    async with session_scope() as session:
        found = await users_with_permission(session, "no_such_permission")
    assert [u.email for u in found] == []
