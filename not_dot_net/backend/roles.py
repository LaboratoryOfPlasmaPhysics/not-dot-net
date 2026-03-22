from enum import Enum as PyEnum


class Role(str, PyEnum):
    MEMBER = "member"
    STAFF = "staff"
    DIRECTOR = "director"
    ADMIN = "admin"


_ROLE_ORDER = {Role.MEMBER: 0, Role.STAFF: 1, Role.DIRECTOR: 2, Role.ADMIN: 3}


def has_role(user, minimum_role: Role) -> bool:
    """Check if user has at least the given role level."""
    user_role = user.role if isinstance(user.role, Role) else Role(user.role)
    return _ROLE_ORDER[user_role] >= _ROLE_ORDER[minimum_role]
