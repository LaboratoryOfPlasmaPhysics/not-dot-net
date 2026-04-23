import uuid
from datetime import date

from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    full_name: str | None = None
    phone: str | None = None
    office: str | None = None
    team: str | None = None
    title: str | None = None
    employment_status: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    role: str = ""


class UserCreate(schemas.BaseUserCreate):
    pass


class UserUpdate(schemas.BaseUserUpdate):
    full_name: str | None = None
    phone: str | None = None
    office: str | None = None
    team: str | None = None
    title: str | None = None
    employment_status: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    role: str | None = None
    company: str | None = None
    description: str | None = None
    webpage: str | None = None
