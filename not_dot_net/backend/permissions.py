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


@dataclass(frozen=True)
class _SystemActor:
    """The application itself acting with no user behind it.

    Services refuse a missing actor, so an internal caller with genuinely no
    user — the workflow engine recording a tenure on completion, dev seeding —
    passes this instead. It has to be named and imported, which keeps such
    calls greppable; omitting the argument no longer silently authorizes.
    """
    id = None
    email = None
    role = ""
    is_superuser = True


SYSTEM_ACTOR = _SystemActor()


async def has_permissions(user, *permissions: str) -> bool:
    """Check if user's role grants all given permissions.

    No user means no permissions. Authorization is never opt-in: a caller that
    passes None gets refused rather than skipping the check.
    """
    if user is None:
        return False
    if getattr(user, "is_superuser", False):
        return True
    from not_dot_net.backend.roles import roles_config
    cfg = await roles_config.get()
    role_def = cfg.roles.get(getattr(user, "role", None))
    if role_def is None:
        return False
    return all(p in role_def.permissions for p in permissions)



async def users_with_permission(session, permission: str) -> list:
    """Active users whose role grants `permission`, plus every super-user.

    Resolves roles first and filters in SQL. The previous shape — load every
    active user, then await has_permissions on each — scanned the whole table
    and, since User.photo is a non-deferred LargeBinary, dragged every profile
    photo through memory on each notification fan-out.
    """
    from sqlalchemy import or_, select
    from sqlalchemy.orm import defer

    from not_dot_net.backend.db import User
    from not_dot_net.backend.roles import roles_config

    cfg = await roles_config.get()
    granting = [
        key for key, definition in cfg.roles.items()
        if permission in definition.permissions
    ]

    condition = User.is_superuser.is_(True)
    if granting:
        condition = or_(condition, User.role.in_(granting))

    result = await session.execute(
        select(User)
        .where(User.is_active.is_(True), condition)
        .options(defer(User.photo))
    )
    return list(result.scalars().all())


def require(*permissions: str):
    """FastAPI dependency — raises 403 if user lacks permissions."""
    from not_dot_net.backend.users import current_active_user

    async def checker(user=Depends(current_active_user)):
        if not await has_permissions(user, *permissions):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return checker


async def check_permission(user, *permissions: str) -> None:
    """Guard for NiceGUI callbacks and service entry points.

    Raises PermissionError on failure, including when `user` is None.
    """
    if user is None:
        raise PermissionError("No actor provided")
    if not await has_permissions(user, *permissions):
        raise PermissionError("Insufficient permissions")
