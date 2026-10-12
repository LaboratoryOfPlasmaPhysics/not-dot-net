import pytest
from contextlib import asynccontextmanager
from fastapi import HTTPException

from not_dot_net.backend.permissions import (
    PermissionInfo,
    permission,
    get_permissions,
    _registry,
    has_permissions,
    check_permission,
    require,
)
from not_dot_net.backend.roles import RoleDefinition, RolesConfig, roles_config
from not_dot_net.backend.db import User, session_scope, get_user_db
from not_dot_net.backend.users import get_user_manager
from not_dot_net.backend.schemas import UserCreate, UserUpdate


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate registry between tests."""
    saved = dict(_registry)
    _registry.clear()
    yield
    _registry.clear()
    _registry.update(saved)


def test_permission_registers_and_returns_key():
    key = permission("do_thing", "Do Thing", "Can do the thing")
    assert key == "do_thing"
    assert "do_thing" in get_permissions()
    info = get_permissions()["do_thing"]
    assert isinstance(info, PermissionInfo)
    assert info.label == "Do Thing"
    assert info.description == "Can do the thing"


def test_get_permissions_returns_all():
    permission("a", "A")
    permission("b", "B")
    assert set(get_permissions().keys()) == {"a", "b"}


def test_duplicate_registration_overwrites():
    permission("x", "X1")
    permission("x", "X2")
    assert get_permissions()["x"].label == "X2"


async def test_has_permissions_granted():
    cfg = await roles_config.get()
    cfg.roles["tester"] = RoleDefinition(label="Tester", permissions=["perm_a", "perm_b"])
    await roles_config.set(cfg)

    class FakeUser:
        role = "tester"

    assert await has_permissions(FakeUser(), "perm_a") is True
    assert await has_permissions(FakeUser(), "perm_a", "perm_b") is True


async def test_has_permissions_denied():
    cfg = await roles_config.get()
    cfg.roles["limited"] = RoleDefinition(label="Limited", permissions=["perm_a"])
    await roles_config.set(cfg)

    class FakeUser:
        role = "limited"

    assert await has_permissions(FakeUser(), "perm_a", "perm_c") is False


async def test_has_permissions_unknown_role():
    class FakeUser:
        role = "nonexistent"

    assert await has_permissions(FakeUser(), "anything") is False


async def test_check_permission_raises_on_denial():
    class FakeUser:
        role = "nonexistent"

    with pytest.raises(PermissionError):
        await check_permission(FakeUser(), "anything")


async def test_check_permission_passes_when_granted():
    cfg = await roles_config.get()
    cfg.roles["ok_role"] = RoleDefinition(label="OK", permissions=["allowed"])
    await roles_config.set(cfg)

    class FakeUser:
        role = "ok_role"

    await check_permission(FakeUser(), "allowed")  # should not raise


async def test_has_permissions_with_no_requested_permission_is_true():
    cfg = await roles_config.get()
    cfg.roles["empty_ok"] = RoleDefinition(label="Empty OK", permissions=[])
    await roles_config.set(cfg)

    class FakeUser:
        role = "empty_ok"

    assert await has_permissions(FakeUser()) is True


async def test_check_permission_raises_when_one_of_multiple_permissions_is_missing():
    cfg = await roles_config.get()
    cfg.roles["partial"] = RoleDefinition(label="Partial", permissions=["perm_a"])
    await roles_config.set(cfg)

    class FakeUser:
        role = "partial"

    with pytest.raises(PermissionError):
        await check_permission(FakeUser(), "perm_a", "perm_b")


async def test_require_returns_user_when_permissions_are_granted():
    cfg = await roles_config.get()
    cfg.roles["api_ok"] = RoleDefinition(label="API OK", permissions=["perm_a"])
    await roles_config.set(cfg)

    class FakeUser:
        role = "api_ok"

    checker = require("perm_a")
    user = FakeUser()
    assert await checker(user=user) is user


async def test_require_raises_http_403_when_permissions_are_missing():
    cfg = await roles_config.get()
    cfg.roles["api_limited"] = RoleDefinition(label="API Limited", permissions=[])
    await roles_config.set(cfg)

    class FakeUser:
        role = "api_limited"

    checker = require("perm_a")
    with pytest.raises(HTTPException) as exc:
        await checker(user=FakeUser())

    assert exc.value.status_code == 403
    assert exc.value.detail == "Insufficient permissions"


async def _create_user(email="user@test.com", password="Password1!") -> User:
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                return await manager.create(UserCreate(email=email, password=password))


async def _update_user(user_id, updates: dict):
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                user = await manager.get(user_id)
                update_schema = UserUpdate(**updates)
                await manager.update(update_schema, user)


def _mounted_routes() -> dict[str, set[str]]:
    """Every mounted HTTP route as {path: {METHOD, ...}}.

    Read from the OpenAPI schema rather than `app.routes`: since FastAPI 0.143
    included routers stay wrapped in `_IncludedRouter`, so `app.routes` no
    longer lists their paths and assertions on it pass vacuously.
    """
    from nicegui import app
    from not_dot_net.app import create_app

    app.openapi_schema = None
    if "/auth/login" not in app.openapi()["paths"]:
        create_app()
        app.openapi_schema = None
    return {
        path: {method.upper() for method in methods}
        for path, methods in app.openapi()["paths"].items()
    }


def test_no_public_user_update_routes_are_mounted():
    paths = _mounted_routes()

    assert "/users/me" not in paths
    assert not any(path.startswith("/users/") for path in paths)


def test_no_public_patch_route_exposes_user_modification():
    patch_paths = {path for path, methods in _mounted_routes().items() if "PATCH" in methods}

    assert "/users/me" not in patch_paths
    assert not any(path.startswith("/users/") for path in patch_paths)


def test_only_expected_auth_http_routes_are_exposed():
    auth_routes = {
        path: methods for path, methods in _mounted_routes().items()
        if path.startswith("/auth") or path == "/logout"
    }

    assert auth_routes == {"/auth/login": {"POST"}, "/logout": {"GET"}}


async def test_role_update_does_not_grant_is_superuser():
    """Setting any role (including legacy 'admin' string) must never flip is_superuser.
    is_superuser is a server-admin flag set only via CLI / bootstrap."""
    user = await _create_user("role-no-escalation@test.com", "Password1!")

    await _update_user(user.id, {"role": "admin"})

    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.role == "admin"
        assert refreshed.is_superuser is False


async def test_role_update_preserves_is_superuser():
    """Role changes leave is_superuser untouched in either direction."""
    user = await _create_user("role-preserve@test.com", "Password1!")

    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        refreshed.is_superuser = True
        await session.commit()

    await _update_user(user.id, {"role": "member"})

    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.role == "member"
        assert refreshed.is_superuser is True


# --- Review 2026-10-12 S4: an open tab keeps the User captured at page load ---


async def _persisted(email: str, **fields) -> User:
    async with session_scope() as session:
        user = User(email=email, hashed_password="x", is_active=True, **fields)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _set(user_id, **fields) -> None:
    async with session_scope() as session:
        db_user = await session.get(User, user_id)
        for k, v in fields.items():
            setattr(db_user, k, v)
        await session.commit()


async def test_demoted_user_loses_permissions_in_open_tab():
    await roles_config.set(RolesConfig(roles={"admin": RoleDefinition(label="A", permissions=["manage_users"])}))
    tab_user = await _persisted("demoted@test.com", role="admin")
    await _set(tab_user.id, role="")
    assert not await has_permissions(tab_user, "manage_users")


async def test_revoked_superuser_loses_bypass_in_open_tab():
    tab_user = await _persisted("ex-root@test.com", is_superuser=True)
    await _set(tab_user.id, is_superuser=False)
    assert not await has_permissions(tab_user, "manage_users")


async def test_deactivated_user_loses_permissions_in_open_tab():
    tab_user = await _persisted("gone@test.com", is_superuser=True)
    await _set(tab_user.id, is_active=False)
    assert not await has_permissions(tab_user, "manage_users")


async def test_system_actor_still_bypasses():
    from not_dot_net.backend.permissions import SYSTEM_ACTOR
    assert await has_permissions(SYSTEM_ACTOR, "manage_users")
