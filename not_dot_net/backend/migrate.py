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
    """True if migrations manage this database, False if it is empty.

    Tables without alembic_version come from create_all (dev mode): which
    revision they match is unknown, so refuse rather than replay 0001.
    """
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            versioned, has_tables = await conn.run_sync(
                lambda c: (sa_inspect(c).has_table("alembic_version"), sa_inspect(c).has_table("user"))
            )
    finally:
        await engine.dispose()
    if has_tables and not versioned:
        raise RuntimeError(
            "Database has tables but no alembic_version. If its schema matches the code, "
            "mark it with `python -m not_dot_net.cli stamp head`; otherwise stamp the matching revision."
        )
    return versioned


async def _create_all(database_url: str) -> None:
    """Create the whole schema from model metadata on a fresh database."""
    from not_dot_net.backend.db import Base
    from not_dot_net.backend.models import register_all_models
    register_all_models()

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
