"""A fresh production database must bootstrap its full schema.

The 0001 baseline is intentionally empty, and prod runs only migrations (never
create_all), so run_upgrade alone dies at 0002 (`no such table: user`) on a
brand-new database. bootstrap_schema must handle the empty case.
"""
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


async def _tables(url: str) -> set[str]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(lambda c: set(sa_inspect(c).get_table_names()))
    finally:
        await engine.dispose()


async def _columns(url: str, table: str) -> set[str]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda c: {col["name"] for col in sa_inspect(c).get_columns(table)}
            )
    finally:
        await engine.dispose()


async def _version(url: str) -> str:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            res = await conn.execute(text("select version_num from alembic_version"))
            return res.scalar()
    finally:
        await engine.dispose()


async def test_bootstrap_initializes_a_fresh_database(tmp_path):
    from alembic.script import ScriptDirectory
    from not_dot_net.backend.migrate import bootstrap_schema, _alembic_config

    url = f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}"

    await bootstrap_schema(url)

    tables = await _tables(url)
    assert {"user", "mail_outbox", "resource", "uid_allocation"} <= tables

    # Head-level schema: resource.status arrived in migration 0016.
    assert "status" in await _columns(url, "resource")

    # Stamped at head so a subsequent migrate is a clean no-op.
    head = ScriptDirectory.from_config(_alembic_config(url)).get_current_head()
    assert await _version(url) == head


async def test_bootstrap_is_idempotent(tmp_path):
    """Second call takes the run_upgrade path on the now-initialized DB."""
    from not_dot_net.backend.migrate import bootstrap_schema

    url = f"sqlite+aiosqlite:///{tmp_path / 'again.db'}"
    await bootstrap_schema(url)
    await bootstrap_schema(url)  # must not raise

    assert "user" in await _tables(url)


# --- F13 (review 2026-10-12) ---


async def test_migrations_since_0017_run_both_ways_on_sqlite(tmp_path):
    """0018 altered a foreign key outside batch mode, which SQLite can't do."""
    from alembic import command
    from not_dot_net.backend.migrate import bootstrap_schema, _alembic_config

    url = f"sqlite+aiosqlite:///{tmp_path / 'walk.db'}"
    await bootstrap_schema(url)
    cfg = _alembic_config(url)
    command.downgrade(cfg, "0017")
    assert "owner_user_id" not in await _columns(url, "resource")
    command.upgrade(cfg, "head")
    assert "owner_user_id" in await _columns(url, "resource")


async def test_tables_without_alembic_version_is_refused_clearly(tmp_path):
    """A create_all database (dev mode) has tables but no version: running
    every migration from 0001 over it died at 0002 with 'table exists'."""
    import pytest
    from not_dot_net.backend.migrate import bootstrap_schema, _create_all

    url = f"sqlite+aiosqlite:///{tmp_path / 'dev.db'}"
    await _create_all(url)
    with pytest.raises(RuntimeError, match="stamp"):
        await bootstrap_schema(url)
