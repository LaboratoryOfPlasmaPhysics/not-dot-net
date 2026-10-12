"""Directory user edits are authorized at the sink (`_update_user`), not just in the form.

Review 2026-10-12 S1: anyone with `manage_users` could set any role — including
on themselves — because the role select was gated on `manage_users` only.
F1: saving a role-less user sent role=None into a NOT NULL column.
"""

import uuid

import pytest

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import SYSTEM_ACTOR
from not_dot_net.backend.roles import RoleDefinition, RolesConfig, roles_config
from not_dot_net.frontend.directory import _update_user


@pytest.fixture
async def roles():
    await roles_config.set(RolesConfig(roles={
        "secretary": RoleDefinition(label="Secretary", permissions=["manage_users"]),
        "role_admin": RoleDefinition(label="Role admin", permissions=["manage_users", "manage_roles"]),
        "admin": RoleDefinition(label="Admin", permissions=["manage_users", "manage_roles", "manage_settings"]),
    }))


async def _user(email: str, *, role: str = "", is_superuser: bool = False) -> User:
    async with session_scope() as session:
        user = User(
            id=uuid.uuid4(), email=email, hashed_password="x",
            is_active=True, role=role, is_superuser=is_superuser,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _reload(user_id) -> User:
    async with session_scope() as session:
        return await session.get(User, user_id)


async def test_manage_users_alone_cannot_change_own_role(roles):
    secretary = await _user("sec@test.com", role="secretary")
    with pytest.raises(PermissionError):
        await _update_user(secretary.id, {"role": "admin"}, actor=secretary)
    assert (await _reload(secretary.id)).role == "secretary"


async def test_manage_users_alone_cannot_change_someone_elses_role(roles):
    secretary = await _user("sec@test.com", role="secretary")
    other = await _user("other@test.com")
    with pytest.raises(PermissionError):
        await _update_user(other.id, {"role": "admin"}, actor=secretary)


async def test_manage_roles_can_change_role(roles):
    role_admin = await _user("ra@test.com", role="role_admin")
    other = await _user("other@test.com")
    await _update_user(other.id, {"role": "secretary"}, actor=role_admin)
    assert (await _reload(other.id)).role == "secretary"


async def test_manage_users_can_edit_profile_fields_of_others(roles):
    secretary = await _user("sec@test.com", role="secretary")
    other = await _user("other@test.com")
    await _update_user(other.id, {"title": "Engineer"}, actor=secretary)
    assert (await _reload(other.id)).title == "Engineer"


async def test_non_superuser_cannot_edit_a_superuser(roles):
    secretary = await _user("sec@test.com", role="secretary")
    root = await _user("root@test.com", is_superuser=True)
    with pytest.raises(PermissionError):
        await _update_user(root.id, {"email": "sec-alias@test.com"}, actor=secretary)
    assert (await _reload(root.id)).email == "root@test.com"


async def test_user_without_permissions_can_edit_own_self_editable_fields(roles):
    plain = await _user("plain@test.com")
    await _update_user(plain.id, {"phone": "+33123"}, actor=plain)
    assert (await _reload(plain.id)).phone == "+33123"


async def test_user_without_permissions_cannot_edit_own_admin_fields(roles):
    plain = await _user("plain@test.com")
    with pytest.raises(PermissionError):
        await _update_user(plain.id, {"email": "new@test.com"}, actor=plain)


async def test_user_without_permissions_cannot_edit_others(roles):
    plain = await _user("plain@test.com")
    other = await _user("other@test.com")
    with pytest.raises(PermissionError):
        await _update_user(other.id, {"phone": "+33123"}, actor=plain)


async def test_missing_actor_is_refused(roles):
    other = await _user("other@test.com")
    with pytest.raises(PermissionError):
        await _update_user(other.id, {"phone": "+33123"}, actor=None)


async def test_clearing_role_stores_empty_string_not_null(roles):
    """F1: a cleared role select yields None; the column is NOT NULL."""
    other = await _user("other@test.com", role="secretary")
    await _update_user(other.id, {"role": None}, actor=SYSTEM_ACTOR)
    assert (await _reload(other.id)).role == ""
