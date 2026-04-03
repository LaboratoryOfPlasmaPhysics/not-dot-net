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
