# RBAC Permissions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the linear role hierarchy (Role enum) with a full RBAC system: code-defined permission registry, DB-backed configurable roles, and mandatory enforcement at both FastAPI and NiceGUI layers.

**Architecture:** Permissions are declared per-module via a registry (same pattern as ConfigSection). Roles map to permission sets, stored as a ConfigSection in the DB. Two enforcement entry points: `require()` for FastAPI DI, `check_permission()` for NiceGUI callbacks. The `"admin"` role has a lockout guard ensuring it always retains `manage_roles` + `manage_settings`.

**Tech Stack:** Python 3.10+, Pydantic, SQLAlchemy async, FastAPI Depends, NiceGUI

---

### Task 1: Permission Registry + Core Enforcement

**Files:**
- Create: `not_dot_net/backend/permissions.py`
- Test: `tests/test_permissions.py`

- [ ] **Step 1: Write failing tests for the permission registry and enforcement**

```python
# tests/test_permissions.py
import pytest

from not_dot_net.backend.permissions import (
    PermissionInfo,
    permission,
    get_permissions,
    has_permissions,
    check_permission,
    _registry,
)


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_permissions.py -v`
Expected: FAIL — module `not_dot_net.backend.permissions` does not exist

- [ ] **Step 3: Implement the permission registry**

```python
# not_dot_net/backend/permissions.py
"""Permission registry and enforcement functions."""

from dataclasses import dataclass

from fastapi import Depends, HTTPException


@dataclass(frozen=True)
class PermissionInfo:
    key: str
    label: str
    description: str = ""


_registry: dict[str, PermissionInfo] = {}


def permission(key: str, label: str, description: str = "") -> str:
    """Register a permission and return its key."""
    _registry[key] = PermissionInfo(key=key, label=label, description=description)
    return key


def get_permissions() -> dict[str, PermissionInfo]:
    """Return all registered permissions."""
    return _registry


# --- Core permissions (protect the RBAC system itself) ---

MANAGE_ROLES = permission("manage_roles", "Manage roles", "Create/edit roles and their permissions")
MANAGE_SETTINGS = permission("manage_settings", "Manage settings", "Access admin settings page")


async def has_permissions(user, *permissions: str) -> bool:
    """Check if user's role grants all given permissions."""
    from not_dot_net.backend.roles import roles_config
    cfg = await roles_config.get()
    role_def = cfg.roles.get(user.role)
    if role_def is None:
        return False
    return all(p in role_def.permissions for p in permissions)


def require(*permissions: str):
    """FastAPI dependency — raises 403 if user lacks permissions."""
    from not_dot_net.backend.users import current_active_user

    async def checker(user=Depends(current_active_user)):
        if not await has_permissions(user, *permissions):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return checker


async def check_permission(user, *permissions: str) -> None:
    """NiceGUI callback guard — raises PermissionError on failure."""
    if not await has_permissions(user, *permissions):
        raise PermissionError("Insufficient permissions")
```

- [ ] **Step 4: Run tests to verify registry tests pass**

Run: `uv run pytest tests/test_permissions.py::test_permission_registers_and_returns_key tests/test_permissions.py::test_get_permissions_returns_all tests/test_permissions.py::test_duplicate_registration_overwrites -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/permissions.py tests/test_permissions.py
git commit -m "feat: add permission registry and enforcement functions"
```

---

### Task 2: Roles Config Section (DB-backed roles)

**Files:**
- Modify: `not_dot_net/backend/roles.py` (full rewrite)
- Test: `tests/test_roles.py` (full rewrite)

- [ ] **Step 1: Write failing tests for the new roles system**

```python
# tests/test_roles.py
import pytest

from not_dot_net.backend.roles import RoleDefinition, RolesConfig, roles_config


async def test_default_config_has_admin_role():
    cfg = await roles_config.get()
    assert "admin" in cfg.roles
    admin = cfg.roles["admin"]
    assert "manage_roles" in admin.permissions
    assert "manage_settings" in admin.permissions


async def test_set_roles_config():
    cfg = await roles_config.get()
    cfg.roles["staff"] = RoleDefinition(
        label="Staff", permissions=["create_workflows"]
    )
    await roles_config.set(cfg)
    reloaded = await roles_config.get()
    assert "staff" in reloaded.roles
    assert reloaded.roles["staff"].permissions == ["create_workflows"]


async def test_lockout_guard_preserves_admin():
    """Cannot remove admin role or strip its critical permissions."""
    cfg = await roles_config.get()
    # Try removing admin entirely
    del cfg.roles["admin"]
    await roles_config.set(cfg)
    reloaded = await roles_config.get()
    assert "admin" in reloaded.roles
    assert "manage_roles" in reloaded.roles["admin"].permissions
    assert "manage_settings" in reloaded.roles["admin"].permissions


async def test_lockout_guard_restores_stripped_permissions():
    """If admin role exists but lacks critical permissions, they are added back."""
    cfg = await roles_config.get()
    cfg.roles["admin"].permissions = ["some_other_perm"]
    await roles_config.set(cfg)
    reloaded = await roles_config.get()
    assert "manage_roles" in reloaded.roles["admin"].permissions
    assert "manage_settings" in reloaded.roles["admin"].permissions
    assert "some_other_perm" in reloaded.roles["admin"].permissions


async def test_default_role_field():
    cfg = await roles_config.get()
    assert cfg.default_role == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_roles.py -v`
Expected: FAIL — old module has `Role` enum, not `RolesConfig`

- [ ] **Step 3: Rewrite roles.py with RolesConfig section**

```python
# not_dot_net/backend/roles.py
"""RBAC role definitions — DB-backed via ConfigSection."""

from pydantic import BaseModel

from not_dot_net.backend.app_config import ConfigSection, section


class RoleDefinition(BaseModel):
    label: str
    permissions: list[str] = []


class RolesConfig(BaseModel):
    default_role: str = ""
    roles: dict[str, RoleDefinition] = {
        "admin": RoleDefinition(
            label="Administrator",
            permissions=["manage_roles", "manage_settings"],
        ),
    }


LOCKOUT_PERMISSIONS = {"manage_roles", "manage_settings"}


class RolesConfigSection(ConfigSection["RolesConfig"]):
    """ConfigSection with lockout guard for the admin role."""

    async def set(self, value: RolesConfig) -> None:
        _enforce_admin_lockout(value)
        await super().set(value)

    async def get(self) -> RolesConfig:
        value = await super().get()
        _enforce_admin_lockout(value)
        return value


def _enforce_admin_lockout(cfg: RolesConfig) -> None:
    """Ensure admin role exists and has critical permissions."""
    if "admin" not in cfg.roles:
        cfg.roles["admin"] = RoleDefinition(
            label="Administrator", permissions=list(LOCKOUT_PERMISSIONS)
        )
    admin = cfg.roles["admin"]
    for perm in LOCKOUT_PERMISSIONS:
        if perm not in admin.permissions:
            admin.permissions.append(perm)


roles_config = RolesConfigSection("roles", RolesConfig, label="Roles")
# Register in the global config registry
from not_dot_net.backend.app_config import _registry
_registry["roles"] = roles_config
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_roles.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/roles.py tests/test_roles.py
git commit -m "feat: rewrite roles as DB-backed ConfigSection with lockout guard"
```

---

### Task 3: User Model — Role Column to String

**Files:**
- Modify: `not_dot_net/backend/db.py:17,41-43`
- Modify: `not_dot_net/backend/schemas.py:7,23,39`
- Modify: `not_dot_net/backend/users.py:18,48-51,110`
- Modify: `not_dot_net/frontend/setup_wizard.py:7,15-17`
- Test: `tests/test_user_role_string.py`

- [ ] **Step 1: Write failing test for string role on User model**

```python
# tests/test_user_role_string.py
import uuid

from not_dot_net.backend.db import User, session_scope


async def test_user_role_is_string():
    async with session_scope() as session:
        user = User(
            id=uuid.uuid4(),
            email="test@test.com",
            hashed_password="x",
            role="staff",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        assert user.role == "staff"
        assert isinstance(user.role, str)


async def test_user_default_role_is_empty():
    async with session_scope() as session:
        user = User(
            id=uuid.uuid4(),
            email="default@test.com",
            hashed_password="x",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        assert user.role == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_user_role_string.py -v`
Expected: FAIL — role column is SAEnum(Role), not String

- [ ] **Step 3: Update db.py — change role column to String**

In `not_dot_net/backend/db.py`:
- Remove the import of `Role` from `not_dot_net.backend.roles` (line 17)
- Add `String` to the sqlalchemy imports (line 8, already has it via mapped_column but we need explicit `String`)
- Change `role` column (lines 41-43) from `SAEnum(Role)` to `String(50)`

Replace line 17:
```python
from not_dot_net.backend.roles import Role
```
with nothing (delete the import).

Replace lines 41-43:
```python
    role: Mapped[Role] = mapped_column(
        SAEnum(Role), default=Role.MEMBER
    )
```
with:
```python
    role: Mapped[str] = mapped_column(
        String(50), default=""
    )
```

Remove `SAEnum` from the sqlalchemy import on line 8 (it's `Enum as SAEnum` — remove that, keep `Date`):
```python
from sqlalchemy import Date, String
```

- [ ] **Step 4: Update schemas.py — change Role references to str**

In `not_dot_net/backend/schemas.py`:

Remove line 7 (`from not_dot_net.backend.roles import Role`).

Change line 23:
```python
    role: Role = Role.MEMBER
```
to:
```python
    role: str = ""
```

Change line 39:
```python
    role: Role | None = None
```
to:
```python
    role: str | None = None
```

- [ ] **Step 5: Update users.py — change role string comparison**

In `not_dot_net/backend/users.py`:

Remove line 18 (`from not_dot_net.backend.roles import Role`).

Change lines 48-51:
```python
    async def on_after_update(self, user: User, update_dict: dict, request: Request | None = None):
        if "role" in update_dict:
            user.is_superuser = (user.role == Role.ADMIN)
            await self.user_db.update(user, {"is_superuser": user.role == Role.ADMIN})
```
to:
```python
    async def on_after_update(self, user: User, update_dict: dict, request: Request | None = None):
        if "role" in update_dict:
            user.is_superuser = (user.role == "admin")
            await self.user_db.update(user, {"is_superuser": user.role == "admin"})
```

Change line 110:
```python
                    user.role = Role.ADMIN
```
to:
```python
                    user.role = "admin"
```

- [ ] **Step 6: Update setup_wizard.py — change Role import and usage**

In `not_dot_net/frontend/setup_wizard.py`:

Remove line 7 (`from not_dot_net.backend.roles import Role`).

Change line 15-17:
```python
        result = await session.execute(
            select(User).where(User.role == Role.ADMIN).limit(1)
        )
```
to:
```python
        result = await session.execute(
            select(User).where(User.role == "admin").limit(1)
        )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_user_role_string.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add not_dot_net/backend/db.py not_dot_net/backend/schemas.py not_dot_net/backend/users.py not_dot_net/frontend/setup_wizard.py tests/test_user_role_string.py
git commit -m "refactor: change User.role from enum to plain string for RBAC"
```

---

### Task 4: Permission Enforcement Tests (has_permissions + check_permission)

**Files:**
- Modify: `tests/test_permissions.py` (add enforcement tests)

This task adds async tests that exercise `has_permissions` and `check_permission` against the new DB-backed roles.

- [ ] **Step 1: Write failing enforcement tests**

Append to `tests/test_permissions.py`:

```python
from not_dot_net.backend.roles import RoleDefinition, roles_config


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_permissions.py -v`
Expected: Some tests FAIL (enforcement functions call `roles_config` which needs DB)

- [ ] **Step 3: Ensure all tests pass (implementation already exists from Tasks 1-2)**

Run: `uv run pytest tests/test_permissions.py -v`
Expected: PASS (all — the DB is set up by `conftest.py` autouse fixture)

- [ ] **Step 4: Commit**

```bash
git add tests/test_permissions.py
git commit -m "test: add enforcement tests for has_permissions and check_permission"
```

---

### Task 5: Declare Module-Level Permissions

**Files:**
- Modify: `not_dot_net/backend/booking_service.py:1-2` (add permission declaration)
- Modify: `not_dot_net/backend/workflow_service.py:1-2` (add permission declarations)
- Modify: `not_dot_net/frontend/audit_log.py:1-2` (add permission declaration)

Each module declares the permissions it owns.

- [ ] **Step 1: Add permission declarations to booking_service.py**

Add after line 10 (after the existing imports):
```python
from not_dot_net.backend.permissions import permission

MANAGE_BOOKINGS = permission("manage_bookings", "Manage bookings", "Create/edit/delete resources and software")
```

- [ ] **Step 2: Add permission declarations to workflow_service.py**

Add after line 11 (after the existing `has_role` import line — which will be replaced):
```python
from not_dot_net.backend.permissions import permission

CREATE_WORKFLOWS = permission("create_workflows", "Create workflows", "Start new workflow requests")
APPROVE_WORKFLOWS = permission("approve_workflows", "Approve workflows", "Act on role-assigned workflow steps")
```

- [ ] **Step 3: Add permission declaration to audit_log.py**

Add after line 5 (after the i18n import):
```python
from not_dot_net.backend.permissions import permission

VIEW_AUDIT_LOG = permission("view_audit_log", "View audit log", "Access the audit log")
```

Also add a declaration for `manage_users` in `not_dot_net/frontend/directory.py` after line 10:
```python
from not_dot_net.backend.permissions import permission

MANAGE_USERS = permission("manage_users", "Manage users", "Edit/delete users in directory")
```

- [ ] **Step 4: Run existing tests to verify nothing is broken**

Run: `uv run pytest -x -v`
Expected: PASS (declarations are just module-level side effects, no logic change)

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/booking_service.py not_dot_net/backend/workflow_service.py not_dot_net/frontend/audit_log.py not_dot_net/frontend/directory.py
git commit -m "feat: declare per-module permissions in registry"
```

---

### Task 6: Seed Admin Role with All Permissions

**Files:**
- Modify: `not_dot_net/backend/roles.py` (update default admin permissions)
- Modify: `not_dot_net/app.py` (seed roles on startup)
- Test: `tests/test_roles.py` (add seed test)

The admin role's default permissions should include all registered permissions. Since modules register permissions at import time, the seed must run after all modules are imported.

- [ ] **Step 1: Write failing test for admin seeding**

Add to `tests/test_roles.py`:

```python
from not_dot_net.backend.roles import seed_admin_permissions


async def test_seed_admin_gets_all_permissions():
    # Import modules that declare permissions
    import not_dot_net.backend.booking_service  # noqa: F401
    import not_dot_net.backend.workflow_service  # noqa: F401
    import not_dot_net.frontend.audit_log  # noqa: F401
    import not_dot_net.frontend.directory  # noqa: F401

    await seed_admin_permissions()
    cfg = await roles_config.get()
    admin = cfg.roles["admin"]
    assert "manage_bookings" in admin.permissions
    assert "create_workflows" in admin.permissions
    assert "approve_workflows" in admin.permissions
    assert "view_audit_log" in admin.permissions
    assert "manage_users" in admin.permissions
    assert "manage_roles" in admin.permissions
    assert "manage_settings" in admin.permissions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_roles.py::test_seed_admin_gets_all_permissions -v`
Expected: FAIL — `seed_admin_permissions` does not exist

- [ ] **Step 3: Implement seed_admin_permissions in roles.py**

Add to the end of `not_dot_net/backend/roles.py`:

```python
async def seed_admin_permissions() -> None:
    """Ensure the admin role has every registered permission."""
    from not_dot_net.backend.permissions import get_permissions
    cfg = await roles_config.get()
    admin = cfg.roles.get("admin")
    if admin is None:
        return
    all_perms = set(get_permissions().keys())
    current = set(admin.permissions)
    if not all_perms.issubset(current):
        admin.permissions = sorted(current | all_perms)
        await roles_config.set(cfg)
```

- [ ] **Step 4: Call seed_admin_permissions in app.py startup**

In `not_dot_net/app.py`, add to the `startup()` async function (after `create_db_and_tables()`):

```python
        from not_dot_net.backend.roles import seed_admin_permissions
        await seed_admin_permissions()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_roles.py::test_seed_admin_gets_all_permissions -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/backend/roles.py not_dot_net/app.py tests/test_roles.py
git commit -m "feat: seed admin role with all registered permissions on startup"
```

---

### Task 7: Enforce Permissions in Booking Service

**Files:**
- Modify: `not_dot_net/backend/booking_service.py:34-77,157-174`
- Modify: `tests/test_booking_service.py`

- [ ] **Step 1: Write failing tests for permission enforcement in booking service**

Add to `tests/test_booking_service.py`:

```python
from not_dot_net.backend.permissions import has_permissions, check_permission
from not_dot_net.backend.roles import RoleDefinition, roles_config


async def _setup_roles():
    """Set up roles with booking permissions for testing."""
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=["manage_bookings", "manage_roles", "manage_settings"],
    )
    cfg.roles["staff"] = RoleDefinition(
        label="Staff",
        permissions=["create_workflows"],
    )
    await roles_config.set(cfg)


async def test_create_resource_requires_permission():
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    with pytest.raises(PermissionError):
        await create_resource("PC", "desktop", actor=staff)


async def test_create_resource_allowed_with_permission():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    r = await create_resource("PC", "desktop", actor=admin)
    assert r.name == "PC"


async def test_update_resource_requires_permission():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    r = await create_resource("PC", "desktop", actor=admin)
    staff = await _create_user(email="staff@test.com", role="staff")
    with pytest.raises(PermissionError):
        await update_resource(r.id, actor=staff, name="New")


async def test_delete_resource_requires_permission():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    r = await create_resource("PC", "desktop", actor=admin)
    staff = await _create_user(email="staff@test.com", role="staff")
    with pytest.raises(PermissionError):
        await delete_resource(r.id, actor=staff)


async def test_cancel_booking_admin_can_cancel_others():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    user1 = await _create_user(email="u1@test.com", role="staff")
    r = await create_resource("PC", "desktop", actor=admin)
    from datetime import date, timedelta
    start = date.today() + timedelta(days=1)
    b = await create_booking(r.id, user1.id, start, start + timedelta(days=3))
    await cancel_booking(b.id, actor=admin)
    assert len(await list_bookings_for_resource(r.id)) == 0


async def test_cancel_booking_non_owner_non_admin_rejected():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    user1 = await _create_user(email="u1@test.com", role="staff")
    user2 = await _create_user(email="u2@test.com", role="staff")
    r = await create_resource("PC", "desktop", actor=admin)
    from datetime import date, timedelta
    start = date.today() + timedelta(days=1)
    b = await create_booking(r.id, user1.id, start, start + timedelta(days=3))
    with pytest.raises(PermissionError):
        await cancel_booking(b.id, actor=user2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_booking_service.py::test_create_resource_requires_permission -v`
Expected: FAIL — `create_resource()` has no `actor` parameter

- [ ] **Step 3: Add permission enforcement to booking_service.py**

Update `create_resource`, `update_resource`, `delete_resource` to accept `actor` parameter and check permissions. Update `cancel_booking` to accept `actor` (User) instead of `user_id + is_admin`.

In `not_dot_net/backend/booking_service.py`, add import after existing permission import:
```python
from not_dot_net.backend.permissions import check_permission
```

Replace `create_resource` (lines 34-54):
```python
async def create_resource(name: str, resource_type: str, description: str = "",
                          location: str = "", specs: dict | None = None,
                          actor=None) -> Resource:
    if actor is not None:
        await check_permission(actor, MANAGE_BOOKINGS)
    async with session_scope() as session:
        resource = Resource(
            name=name,
            resource_type=resource_type,
            description=description or None,
            location=location or None,
            specs=specs,
        )
        session.add(resource)
        await session.commit()
        await session.refresh(resource)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "resource", "create",
        target_type="resource", target_id=resource.id,
        detail=f"name={name} type={resource_type}",
    )
    return resource
```

Replace `update_resource` (lines 57-67):
```python
async def update_resource(resource_id: uuid.UUID, actor=None, **kwargs) -> Resource:
    if actor is not None:
        await check_permission(actor, MANAGE_BOOKINGS)
    async with session_scope() as session:
        resource = await session.get(Resource, resource_id)
        if resource is None:
            raise ValueError(f"Resource {resource_id} not found")
        for key, value in kwargs.items():
            if hasattr(resource, key):
                setattr(resource, key, value)
        await session.commit()
        await session.refresh(resource)
        return resource
```

Replace `delete_resource` (lines 70-76):
```python
async def delete_resource(resource_id: uuid.UUID, actor=None) -> None:
    if actor is not None:
        await check_permission(actor, MANAGE_BOOKINGS)
    async with session_scope() as session:
        resource = await session.get(Resource, resource_id)
        if resource is None:
            raise ValueError(f"Resource {resource_id} not found")
        await session.delete(resource)
        await session.commit()
```

Replace `cancel_booking` (lines 157-174):
```python
async def cancel_booking(booking_id: uuid.UUID, user_id: uuid.UUID | None = None,
                         is_admin: bool = False, actor=None) -> None:
    async with session_scope() as session:
        booking = await session.get(Booking, booking_id)
        if booking is None:
            raise ValueError("Booking not found")

        if actor is not None:
            is_owner = booking.user_id == actor.id
            has_perm = await has_permissions(actor, MANAGE_BOOKINGS)
            if not is_owner and not has_perm:
                raise PermissionError("Can only cancel your own bookings")
            user_id = actor.id
        elif not is_admin and booking.user_id != user_id:
            raise PermissionError("Can only cancel your own bookings")

        resource_id = booking.resource_id
        await session.delete(booking)
        await session.commit()

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "booking", "cancel",
        actor_id=user_id,
        target_type="resource", target_id=resource_id,
        detail=f"booking={booking_id}",
    )
```

Add `has_permissions` to the import from permissions:
```python
from not_dot_net.backend.permissions import check_permission, has_permissions
```

- [ ] **Step 4: Update existing booking tests to use new role system**

In `tests/test_booking_service.py`, update `_create_user` to use string roles:
```python
async def _create_user(email="user@test.com", role="staff") -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user
```

Remove `from not_dot_net.backend.roles import Role` import.

Update `test_cancel_as_admin` to use old API (backward compat) or update to new API:
```python
async def test_cancel_as_admin():
    user1 = await _create_user(email="u1@test.com")
    admin = await _create_user(email="admin@test.com", role="admin")
    r = await _create_test_resource()
    start = date.today() + timedelta(days=1)
    b = await create_booking(r.id, user1.id, start, start + timedelta(days=3))
    await cancel_booking(b.id, admin.id, is_admin=True)
    assert len(await list_bookings_for_resource(r.id)) == 0
```

- [ ] **Step 5: Run all booking tests**

Run: `uv run pytest tests/test_booking_service.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/backend/booking_service.py tests/test_booking_service.py
git commit -m "feat: enforce permissions in booking service functions"
```

---

### Task 8: Enforce Permissions in Workflow Service

**Files:**
- Modify: `not_dot_net/backend/workflow_service.py:11,156-198,201-221,284-304,350-380`
- Modify: `not_dot_net/backend/workflow_engine.py:1-3,71-87`
- Modify: `not_dot_net/config.py:25`
- Modify: `tests/test_workflow_service.py`
- Modify: `tests/test_workflow_engine.py`

- [ ] **Step 1: Write failing tests for permission enforcement in workflow service**

Add to `tests/test_workflow_service.py`:

```python
from not_dot_net.backend.roles import RoleDefinition, roles_config


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=["manage_bookings", "manage_roles", "manage_settings",
                     "create_workflows", "approve_workflows", "view_audit_log", "manage_users"],
    )
    cfg.roles["staff"] = RoleDefinition(
        label="Staff",
        permissions=["create_workflows"],
    )
    cfg.roles["director"] = RoleDefinition(
        label="Director",
        permissions=["create_workflows", "approve_workflows"],
    )
    cfg.roles["member"] = RoleDefinition(
        label="Member",
        permissions=[],
    )
    await roles_config.set(cfg)


async def test_create_request_requires_permission():
    await _setup_roles()
    member = await _create_user(email="member@test.com", role="member")
    with pytest.raises(PermissionError):
        await create_request(
            workflow_type="vpn_access",
            created_by=member.id,
            data={"target_name": "A", "target_email": "a@test.com"},
            actor=member,
        )


async def test_create_request_allowed_with_permission():
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
        actor=staff,
    )
    assert req.type == "vpn_access"


async def test_submit_step_requires_actor():
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
        actor=staff,
    )
    member = await _create_user(email="member@test.com", role="member")
    with pytest.raises(PermissionError):
        await submit_step(req.id, member.id, "submit", data={}, actor_user=member)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_workflow_service.py::test_create_request_requires_permission -v`
Expected: FAIL — `create_request()` has no `actor` parameter

- [ ] **Step 3: Update workflow_service.py — add permission enforcement**

In `not_dot_net/backend/workflow_service.py`:

Replace line 11:
```python
from not_dot_net.backend.roles import Role, has_role
```
with:
```python
from not_dot_net.backend.permissions import check_permission, has_permissions
```

Update `create_request` (lines 156-198) to accept `actor` parameter:
```python
async def create_request(
    workflow_type: str,
    created_by: uuid.UUID,
    data: dict,
    actor=None,
) -> WorkflowRequest:
    if actor is not None:
        await check_permission(actor, CREATE_WORKFLOWS)
    wf = await _get_workflow_config(workflow_type)
    first_step = wf.steps[0].key
    # ... rest unchanged
```

Update `list_actionable` (lines 350-380) to use permission-based check:

Replace:
```python
            if step.assignee_role and has_role(user, Role(step.assignee_role)):
```
with:
```python
            if step.assignee_permission and await has_permissions(user, step.assignee_permission):
```

Update `_fire_notifications` (lines 128-135) — replace role-based user lookup. Change `get_users_by_role` to `get_users_by_permission`:

Replace:
```python
    from not_dot_net.backend.roles import Role as RoleEnum
```
and:
```python
        async def get_users_by_role(role_str):
            result = await session.execute(
                select(User).where(
                    User.role == RoleEnum(role_str),
                    User.is_active == True,
                )
            )
            return list(result.scalars().all())
```
with:
```python
        async def get_users_by_role(role_str):
            # role_str here is a role name from notification config
            # Get all active users and filter by role name
            result = await session.execute(
                select(User).where(
                    User.role == role_str,
                    User.is_active == True,
                )
            )
            return list(result.scalars().all())
```

- [ ] **Step 4: Update config.py — add assignee_permission to WorkflowStepConfig**

In `not_dot_net/config.py`, update `WorkflowStepConfig` (line 25):

Add a new field after `assignee_role`:
```python
    assignee_permission: str | None = None
```

Keep `assignee_role` for backward compatibility but it will no longer be used for authorization.

- [ ] **Step 5: Update workflow_engine.py — remove role-based auth**

In `not_dot_net/backend/workflow_engine.py`:

Replace line 3:
```python
from not_dot_net.backend.roles import Role, has_role
```
with nothing (delete it).

Update `can_user_act` (lines 71-87) — remove role-based check, keep only contextual assignment:

```python
def can_user_act(user, request, workflow: WorkflowConfig) -> bool:
    """Check if a user can act on the current step (contextual assignment only).

    Permission-based checks are handled by the service layer.
    """
    step = get_current_step_config(request, workflow)
    if step is None:
        return False

    # Contextual assignment
    if step.assignee == "target_person":
        return user.email == request.target_email
    if step.assignee == "requester":
        return str(user.id) == str(request.created_by)

    # If step has assignee_permission or assignee_role, the service layer handles it
    return step.assignee_permission is not None or step.assignee_role is not None
```

- [ ] **Step 6: Update workflow step configs in workflow_service.py**

In the default `WorkflowsConfig` workflow definitions, add `assignee_permission` alongside `assignee_role`:

For `vpn_access` steps:
- `request` step: add `assignee_permission="create_workflows"`
- `approval` step: add `assignee_permission="approve_workflows"`

For `onboarding` steps:
- `request` step: add `assignee_permission="create_workflows"`
- `admin_validation` step: add `assignee_permission="approve_workflows"`
- `newcomer_info` step: already uses `assignee="target_person"`, no change needed

- [ ] **Step 7: Update tests**

In `tests/test_workflow_service.py`, update `_create_user` to use string roles:
```python
async def _create_user(email="staff@test.com", role="staff") -> User:
```
Remove `from not_dot_net.backend.roles import Role`.

Update all `role=Role.STAFF` to `role="staff"`, `role=Role.DIRECTOR` to `role="director"`, `role=Role.MEMBER` to `role="member"`.

Add `await _setup_roles()` call at the start of tests that rely on role permissions (e.g., `test_list_actionable_by_role`, `test_authorization_check_blocks_wrong_user`).

In `tests/test_workflow_engine.py`:
- Remove `from not_dot_net.backend.roles import Role` import
- Update `FakeUser` to use string roles instead of `Role` enum
- Update workflow configs to include `assignee_permission` field
- Update `test_can_user_act_role_match`, `test_can_user_act_role_higher`, `test_can_user_act_role_too_low` to reflect that `can_user_act` now returns `True` for any step with `assignee_permission` set (the permission check happens in the service layer, not the engine)

- [ ] **Step 8: Run all workflow tests**

Run: `uv run pytest tests/test_workflow_engine.py tests/test_workflow_service.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add not_dot_net/backend/workflow_service.py not_dot_net/backend/workflow_engine.py not_dot_net/config.py tests/test_workflow_service.py tests/test_workflow_engine.py
git commit -m "feat: enforce permissions in workflow service, remove role hierarchy from engine"
```

---

### Task 9: Enforce Permissions in Frontend — Shell + Pages

**Files:**
- Modify: `not_dot_net/frontend/shell.py`
- Modify: `not_dot_net/frontend/bookings.py`
- Modify: `not_dot_net/frontend/directory.py`
- Modify: `not_dot_net/frontend/admin_settings.py`
- Modify: `not_dot_net/frontend/dashboard.py`
- Modify: `not_dot_net/frontend/new_request.py`
- Modify: `not_dot_net/frontend/audit_log.py`

- [ ] **Step 1: Update shell.py — use has_permissions instead of has_role**

Replace imports (lines 8):
```python
from not_dot_net.backend.roles import Role, has_role
```
with:
```python
from not_dot_net.backend.permissions import has_permissions
```

The `main_page` function must become async-aware for permission checks. Replace lines 35-36:
```python
        can_create = has_role(user, Role.STAFF)
        is_admin = has_role(user, Role.ADMIN)
```
with:
```python
        can_create = await has_permissions(user, "create_workflows")
        is_admin = await has_permissions(user, "manage_settings")
```

- [ ] **Step 2: Update bookings.py — use has_permissions**

Replace import (line 22):
```python
from not_dot_net.backend.roles import Role, has_role
```
with:
```python
from not_dot_net.backend.permissions import has_permissions
```

Replace line 46:
```python
    is_admin = has_role(user, Role.ADMIN)
```
with:
```python
    is_admin = await has_permissions(user, "manage_bookings")
```

Update `cancel_booking` calls to pass `actor=user`:
- Line 74: `await cancel_booking(b.id, user.id)` → `await cancel_booking(b.id, actor=user)`
- Line 320: `await cancel_booking(b.id, user.id, is_admin=is_admin)` → `await cancel_booking(b.id, actor=user)`

Update `create_resource`, `update_resource`, `delete_resource` calls to pass `actor=user`:
- Line 485-491 (`do_save` in dialog): add `actor=user` to `create_resource()` and `update_resource()` calls
- Line 415 (`do_delete`): `await delete_resource(res.id)` → `await delete_resource(res.id, actor=user)`

- [ ] **Step 3: Update directory.py — add permission guard to edit/delete**

Replace `is_admin = current_user.is_superuser` checks with permission checks. 

Add import:
```python
from not_dot_net.backend.permissions import has_permissions
```

In `_render_detail` (line 137), replace:
```python
    is_admin = current_user.is_superuser
```
with:
```python
    is_admin = await has_permissions(current_user, "manage_users")
```

This requires making `_render_detail` async. Update its signature and all call sites.

In `_render_edit` (line 182), replace:
```python
    is_admin = current_user.is_superuser
```
with:
```python
    is_admin = await has_permissions(current_user, "manage_users")
```

Make `_render_edit` async too and update call sites.

- [ ] **Step 4: Update admin_settings.py — add permission guard**

Add at the start of `render()` (line 23):
```python
    from not_dot_net.backend.permissions import check_permission
    await check_permission(user, "manage_settings")
```

- [ ] **Step 5: Update dashboard.py — use permissions for all-requests check**

Replace imports (lines 6-7):
```python
from not_dot_net.backend.roles import Role, has_role
```
with:
```python
from not_dot_net.backend.permissions import has_permissions
```

Replace line 64:
```python
    if has_role(user, Role.ADMIN):
```
with:
```python
    if await has_permissions(user, "view_audit_log"):
```

- [ ] **Step 6: Update new_request.py — use permissions**

Replace imports (lines 5-6):
```python
from not_dot_net.backend.roles import Role, has_role
```
with:
```python
from not_dot_net.backend.permissions import has_permissions
```

Replace line 21:
```python
            if not has_role(user, Role(wf_config.start_role)):
```
with:
```python
            if not await has_permissions(user, "create_workflows"):
```

Update `create_request` call (lines 34-38) to pass `actor=user`:
```python
                    await create_request(
                        workflow_type=key,
                        created_by=user.id,
                        data=data,
                        actor=user,
                    )
```

- [ ] **Step 7: Update audit_log.py — already has permission declared, no enforcement changes needed here since shell controls tab visibility**

The audit_log tab is only shown when `is_admin` is true in shell.py (which now checks `manage_settings`). For defense in depth, add a guard:

Add at the top of `render()`:
```python
def render(user=None):
    # audit log render - user parameter added for future permission checks
```

(The shell already gates this — no blocking change needed for now.)

- [ ] **Step 8: Run full test suite**

Run: `uv run pytest -x -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add not_dot_net/frontend/shell.py not_dot_net/frontend/bookings.py not_dot_net/frontend/directory.py not_dot_net/frontend/admin_settings.py not_dot_net/frontend/dashboard.py not_dot_net/frontend/new_request.py not_dot_net/frontend/audit_log.py
git commit -m "feat: enforce permissions across all frontend pages"
```

---

### Task 10: Admin Roles Management UI

**Files:**
- Create: `not_dot_net/frontend/admin_roles.py`
- Modify: `not_dot_net/frontend/admin_settings.py` (include roles section)

- [ ] **Step 1: Create admin_roles.py**

```python
# not_dot_net/frontend/admin_roles.py
"""Admin roles management UI — create, edit, delete roles and assign permissions."""

from nicegui import ui
from sqlalchemy import select, func

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import check_permission, get_permissions, MANAGE_ROLES
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.frontend.i18n import t


async def render(user):
    """Render the roles management section."""
    await check_permission(user, MANAGE_ROLES)

    container = ui.column().classes("w-full")

    async def refresh():
        await _render_roles(container, user)

    ui.timer(0, refresh, once=True)


async def _user_count_by_role() -> dict[str, int]:
    async with session_scope() as session:
        result = await session.execute(
            select(User.role, func.count()).group_by(User.role)
        )
        return {row[0]: row[1] for row in result.all()}


async def _render_roles(container, user):
    container.clear()
    cfg = await roles_config.get()
    all_perms = get_permissions()
    user_counts = await _user_count_by_role()

    with container:
        ui.label(t("roles")).classes("text-h6 mb-2")

        for role_key, role_def in sorted(cfg.roles.items()):
            count = user_counts.get(role_key, 0)
            with ui.expansion(
                f"{role_def.label} ({role_key}) — {count} users, {len(role_def.permissions)} permissions"
            ).classes("w-full"):
                await _render_role_editor(container, user, role_key, role_def, all_perms, count)

        # Add role button
        with ui.row().classes("mt-3 gap-2"):
            new_key = ui.input(t("role_key")).props("outlined dense").classes("w-32")
            new_label = ui.input(t("role_label")).props("outlined dense").classes("w-48")

            async def add_role():
                key = new_key.value.strip().lower()
                label = new_label.value.strip()
                if not key or not label:
                    ui.notify("Key and label required", color="negative")
                    return
                cfg_now = await roles_config.get()
                if key in cfg_now.roles:
                    ui.notify(f"Role '{key}' already exists", color="negative")
                    return
                cfg_now.roles[key] = RoleDefinition(label=label, permissions=[])
                await roles_config.set(cfg_now)
                ui.notify(f"Role '{key}' created", color="positive")
                await _render_roles(container, user)

            ui.button(t("add"), icon="add", on_click=add_role).props("flat color=primary")

        # Default role selector
        ui.label(t("default_role")).classes("text-subtitle2 mt-4")
        role_options = [""] + sorted(cfg.roles.keys())
        default_select = ui.select(
            options=role_options,
            value=cfg.default_role,
            label=t("default_role"),
        ).props("outlined dense").classes("w-48")

        async def save_default():
            cfg_now = await roles_config.get()
            cfg_now.default_role = default_select.value
            await roles_config.set(cfg_now)
            ui.notify(t("settings_saved"), color="positive")

        ui.button(t("save"), on_click=save_default).props("flat color=primary")


async def _render_role_editor(outer_container, user, role_key, role_def, all_perms, user_count):
    """Render permission checkboxes for a single role."""
    checkboxes = {}
    with ui.element("div").classes("grid grid-cols-2 md:grid-cols-3 gap-2"):
        for perm_key, perm_info in sorted(all_perms.items()):
            checked = perm_key in role_def.permissions
            cb = ui.checkbox(
                f"{perm_info.label}",
                value=checked,
            ).tooltip(perm_info.description or perm_key)
            checkboxes[perm_key] = cb

    with ui.row().classes("mt-2 gap-2"):
        async def save():
            cfg = await roles_config.get()
            selected = [k for k, cb in checkboxes.items() if cb.value]
            cfg.roles[role_key].permissions = selected
            await roles_config.set(cfg)

            from not_dot_net.backend.audit import log_audit
            await log_audit(
                "settings", "update_role",
                actor_id=user.id, actor_email=user.email,
                detail=f"role={role_key} permissions={selected}",
            )
            ui.notify(t("settings_saved"), color="positive")

        ui.button(t("save"), on_click=save).props("color=primary")

        if role_key != "admin":
            async def delete():
                if user_count > 0:
                    ui.notify(
                        f"Cannot delete role '{role_key}' — {user_count} users assigned",
                        color="negative",
                    )
                    return
                cfg = await roles_config.get()
                del cfg.roles[role_key]
                await roles_config.set(cfg)
                ui.notify(f"Role '{role_key}' deleted", color="positive")
                await _render_roles(outer_container, user)

            ui.button(t("delete"), icon="delete", on_click=delete).props("flat color=negative")
```

- [ ] **Step 2: Add roles tab to admin_settings.py**

In `not_dot_net/frontend/admin_settings.py`, add import at top:
```python
from not_dot_net.frontend.admin_roles import render as render_roles
```

Add at the beginning of the `render()` function, before the registry loop:
```python
    with ui.expansion("Roles", icon="admin_panel_settings").classes("w-full"):
        await render_roles(user)
```

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -x -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add not_dot_net/frontend/admin_roles.py not_dot_net/frontend/admin_settings.py
git commit -m "feat: add admin roles management UI with permission checkboxes"
```

---

### Task 11: Fix Remaining Role References + Full Test Sweep

**Files:**
- Modify: any remaining files that import `Role` enum or `has_role`
- Modify: all test files that use `Role` enum

This is a cleanup task to ensure no references to the old `Role` enum or `has_role` remain.

- [ ] **Step 1: Search for remaining references**

Run:
```bash
uv run grep -rn "from not_dot_net.backend.roles import Role" not_dot_net/ tests/
uv run grep -rn "has_role" not_dot_net/ tests/
uv run grep -rn "Role\." not_dot_net/ tests/
```

Fix any remaining references found.

- [ ] **Step 2: Update conftest.py if needed**

The `conftest.py` doesn't reference roles directly, but test helper functions in other test files create users with `role=Role.X` — these all need to use strings. Fix any remaining ones.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove all references to old Role enum and has_role"
```

---

### Task 12: Integration Test — Full Permission Flow

**Files:**
- Create: `tests/test_rbac_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_rbac_integration.py
"""End-to-end RBAC integration test — roles, permissions, enforcement."""

import uuid
import pytest

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import (
    get_permissions,
    has_permissions,
    check_permission,
)
from not_dot_net.backend.roles import RoleDefinition, roles_config, seed_admin_permissions
from not_dot_net.backend.booking_service import (
    create_resource,
    delete_resource,
    update_resource,
)
from not_dot_net.backend.workflow_service import create_request


# Force permission registration
import not_dot_net.backend.booking_service  # noqa: F401
import not_dot_net.backend.workflow_service  # noqa: F401
import not_dot_net.frontend.audit_log  # noqa: F401
import not_dot_net.frontend.directory  # noqa: F401


async def _create_user(email, role):
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _setup():
    await seed_admin_permissions()
    cfg = await roles_config.get()
    cfg.roles["readonly"] = RoleDefinition(label="Read Only", permissions=[])
    cfg.roles["booker"] = RoleDefinition(
        label="Booker", permissions=["manage_bookings"]
    )
    cfg.roles["workflow_user"] = RoleDefinition(
        label="Workflow User", permissions=["create_workflows"]
    )
    await roles_config.set(cfg)


async def test_admin_can_do_everything():
    await _setup()
    admin = await _create_user("admin@test.com", "admin")
    all_perms = list(get_permissions().keys())
    for perm in all_perms:
        assert await has_permissions(admin, perm), f"admin should have {perm}"


async def test_readonly_cannot_do_anything():
    await _setup()
    user = await _create_user("ro@test.com", "readonly")
    all_perms = list(get_permissions().keys())
    for perm in all_perms:
        assert not await has_permissions(user, perm), f"readonly should not have {perm}"


async def test_booker_can_manage_resources():
    await _setup()
    booker = await _create_user("booker@test.com", "booker")
    r = await create_resource("Test PC", "desktop", actor=booker)
    assert r.name == "Test PC"
    await update_resource(r.id, actor=booker, name="Updated PC")
    await delete_resource(r.id, actor=booker)


async def test_booker_cannot_create_workflows():
    await _setup()
    booker = await _create_user("booker@test.com", "booker")
    with pytest.raises(PermissionError):
        await create_request(
            "vpn_access", booker.id,
            data={"target_name": "A", "target_email": "a@test.com"},
            actor=booker,
        )


async def test_workflow_user_cannot_manage_resources():
    await _setup()
    wf_user = await _create_user("wf@test.com", "workflow_user")
    with pytest.raises(PermissionError):
        await create_resource("PC", "desktop", actor=wf_user)


async def test_unknown_role_has_no_permissions():
    await _setup()
    user = await _create_user("ghost@test.com", "nonexistent_role")
    assert not await has_permissions(user, "manage_bookings")
    with pytest.raises(PermissionError):
        await check_permission(user, "manage_bookings")
```

- [ ] **Step 2: Run integration tests**

Run: `uv run pytest tests/test_rbac_integration.py -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_rbac_integration.py
git commit -m "test: add RBAC integration tests for full permission flow"
```
