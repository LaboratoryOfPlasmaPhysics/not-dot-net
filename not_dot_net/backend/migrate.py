"""Run Alembic migrations programmatically."""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


def _alembic_config(database_url: str) -> Config:
    alembic_dir = Path(__file__).resolve().parents[2] / "alembic"
    ini_path = alembic_dir.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(alembic_dir))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def run_upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(_alembic_config(database_url), revision)


def stamp_head(database_url: str) -> None:
    command.stamp(_alembic_config(database_url), "head")


async def _schema_initialized(database_url: str) -> bool:
    """True if the database already holds application tables."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda c: sa_inspect(c).has_table("alembic_version")
                or sa_inspect(c).has_table("user")
            )
    finally:
        await engine.dispose()


async def _create_all(database_url: str) -> None:
    """Create the whole schema from model metadata on a fresh database."""
    from not_dot_net.backend.db import Base
    import not_dot_net.backend.workflow_models  # noqa: F401 — register models
    import not_dot_net.backend.booking_models  # noqa: F401
    import not_dot_net.backend.audit  # noqa: F401
    import not_dot_net.backend.app_config  # noqa: F401
    import not_dot_net.backend.page_models  # noqa: F401
    import not_dot_net.backend.encrypted_storage  # noqa: F401
    import not_dot_net.backend.tenure_service  # noqa: F401
    import not_dot_net.backend.mail_outbox  # noqa: F401
    import not_dot_net.backend.uid_allocator  # noqa: F401
    import not_dot_net.backend.floorplan_models  # noqa: F401 — register FloorPlan/MapPoint with Base
    import not_dot_net.backend.office_availability  # noqa: F401 — register OfficeAvailability with Base

    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


async def bootstrap_schema(database_url: str) -> None:
    """Initialize a fresh database, or migrate an existing one.

    The 0001 baseline is intentionally empty — the original production DB was
    built with create_all and then stamped, so `run_upgrade` alone dies at 0002
    (`no such table: user`) on a brand-new database (new cluster, disaster
    recovery, staging). Detect the empty case and create_all + stamp head; an
    already-initialized DB just runs pending migrations.
    """
    if await _schema_initialized(database_url):
        run_upgrade(database_url)
    else:
        await _create_all(database_url)
        stamp_head(database_url)
