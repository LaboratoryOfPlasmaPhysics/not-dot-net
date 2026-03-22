import uuid

from fastapi_users import schemas

from not_dot_net.backend.roles import Role


class UserRead(schemas.BaseUser[uuid.UUID]):
    full_name: str | None = None
    phone: str | None = None
    office: str | None = None
    team: str | None = None
    title: str | None = None
    employment_status: str | None = None
    role: Role = Role.MEMBER


class UserCreate(schemas.BaseUserCreate):
    pass


class UserUpdate(schemas.BaseUserUpdate):
    full_name: str | None = None
    phone: str | None = None
    office: str | None = None
    team: str | None = None
    title: str | None = None
    employment_status: str | None = None
    role: Role | None = None
