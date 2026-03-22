from collections.abc import AsyncGenerator
from enum import Enum as PyEnum

from fastapi import Depends
from fastapi_users.db import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from sqlalchemy import Enum as SAEnum
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from not_dot_net.backend.roles import Role


class AuthMethod(str, PyEnum):
    LOCAL = "local"
    LDAP = "ldap"


class Base(DeclarativeBase):
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    auth_method: Mapped[AuthMethod] = mapped_column(
        SAEnum(AuthMethod), default=AuthMethod.LOCAL
    )
    full_name: Mapped[str | None] = mapped_column(default=None)
    phone: Mapped[str | None] = mapped_column(default=None)
    office: Mapped[str | None] = mapped_column(default=None)
    team: Mapped[str | None] = mapped_column(default=None)
    title: Mapped[str | None] = mapped_column(default=None)
    employment_status: Mapped[str | None] = mapped_column(default=None)
    role: Mapped[Role] = mapped_column(
        SAEnum(Role), default=Role.MEMBER
    )


_engine: AsyncEngine | None = None
_async_session_maker: async_sessionmaker[AsyncSession] | None = None


def init_db(database_url: str) -> None:
    global _engine, _async_session_maker
    _engine = create_async_engine(database_url)
    _async_session_maker = async_sessionmaker(_engine, expire_on_commit=False)


async def create_db_and_tables() -> None:
    if _engine is None:
        raise RuntimeError("DB not initialized — call init_db() first")
    import not_dot_net.backend.workflow_models  # noqa: F401 — register models with Base
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    if _async_session_maker is None:
        raise RuntimeError("DB not initialized — call init_db() first")
    async with _async_session_maker() as session:
        yield session


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User)
