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
from not_dot_net.backend.roles import RoleDefinition, roles_config
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


def _mounted_routes():
    from nicegui import app
    from not_dot_net.app import create_app

    paths = {getattr(route, "path", None) for route in app.routes}
    if "/login" not in paths and "/auth/login" not in paths:
        create_app()
        paths = {getattr(route, "path", None) for route in app.routes}
    return app.routes, paths


def test_no_public_user_update_routes_are_mounted():
    _, paths = _mounted_routes()

    assert "/users/me" not in paths
    assert not any(path and path.startswith("/users/") for path in paths)


def test_no_public_patch_route_exposes_user_modification():
    app_routes, _ = _mounted_routes()
    patch_paths = {
        getattr(route, "path", None)
        for route in app_routes
        if "PATCH" in getattr(route, "methods", set())
    }

    assert "/users/me" not in patch_paths
    assert not any(path and path.startswith("/users/") for path in patch_paths)


def test_only_expected_auth_http_routes_are_exposed():
    app_routes, _ = _mounted_routes()
    auth_routes = {
        (getattr(route, "path", None), tuple(sorted(getattr(route, "methods", set()))))
        for route in app_routes
        if getattr(route, "path", "").startswith("/auth") or getattr(route, "path", "") == "/logout"
    }

    assert ("/auth/login", ("POST",)) in auth_routes
    assert ("/logout", ("GET",)) in auth_routes
    assert not any(path == "/users/me" for path, _ in auth_routes)
    assert not any(path.startswith("/auth/jwt") or path.startswith("/auth/cookie") for path, _ in auth_routes)


async def test_update_path_role_admin_sets_is_superuser_true():
    user = await _create_user("role-admin@test.com", "Password1!")

    await _update_user(user.id, {"role": "admin"})

    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.role == "admin"
        assert refreshed.is_superuser is True


async def test_update_path_role_member_clears_is_superuser():
    user = await _create_user("role-member@test.com", "Password1!")

    await _update_user(user.id, {"role": "admin"})
    await _update_user(user.id, {"role": "member"})

    async with session_scope() as session:
        refreshed = await session.get(User, user.id)
        assert refreshed.role == "member"
        assert refreshed.is_superuser is False
