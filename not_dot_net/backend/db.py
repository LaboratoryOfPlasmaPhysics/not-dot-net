from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, datetime
from enum import Enum as PyEnum

from fastapi import Depends
from fastapi_users.db import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from sqlalchemy import Date, DateTime, Enum as SAEnum, JSON, LargeBinary, String, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None)
    company: Mapped[str | None] = mapped_column(default=None)
    description: Mapped[str | None] = mapped_column(default=None)
    webpage: Mapped[str | None] = mapped_column(default=None)
    uid_number: Mapped[int | None] = mapped_column(default=None)
    gid_number: Mapped[int | None] = mapped_column(default=None)
    member_of: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    photo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, default=None)
    role: Mapped[str] = mapped_column(
        String(50), default=""
    )
    ldap_dn: Mapped[str | None] = mapped_column(default=None)
    ldap_username: Mapped[str | None] = mapped_column(default=None)
    last_ad_logon: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
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
    import not_dot_net.backend.booking_models  # noqa: F401 — register models with Base
    import not_dot_net.backend.floorplan_models  # noqa: F401 — register FloorPlan/MapPoint with Base
    import not_dot_net.backend.office_availability  # noqa: F401 — register OfficeAvailability with Base
    import not_dot_net.backend.audit  # noqa: F401 — register models with Base
    import not_dot_net.backend.app_config  # noqa: F401 — register AppSetting with Base
    import not_dot_net.backend.page_models  # noqa: F401 — register Page with Base
    import not_dot_net.backend.encrypted_storage  # noqa: F401 — register EncryptedFile with Base
    import not_dot_net.backend.tenure_service  # noqa: F401 — register UserTenure with Base
    import not_dot_net.backend.mail_outbox  # noqa: F401 — register MailOutbox with Base
    import not_dot_net.backend.uid_allocator  # noqa: F401 — register UidAllocation with Base
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    if _async_session_maker is None:
        raise RuntimeError("DB not initialized — call init_db() first")
    async with _async_session_maker() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for use outside FastAPI DI (services, CLI, etc.)."""
    if _async_session_maker is None:
        raise RuntimeError("DB not initialized — call init_db() first")
    async with _async_session_maker() as session:
        yield session


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User)


async def resolve_user_names(user_ids) -> dict:
    """Resolve user ids to display names (full_name or email) in one query."""
    unique_ids = {uid for uid in user_ids if uid is not None}
    if not unique_ids:
        return {}
    async with session_scope() as session:
        result = await session.execute(
            select(User.id, User.full_name, User.email).where(User.id.in_(unique_ids))
        )
        return {row.id: row.full_name or row.email for row in result.all()}
