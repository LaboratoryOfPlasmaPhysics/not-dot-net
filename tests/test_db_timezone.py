"""The database session runs in UTC, whatever the server's timezone.

Review 2026-10-12 V5: production Postgres runs with TimeZone=Europe/Paris, so
server_default=now() columns stored Paris local time while Python wrote UTC.
"""

from not_dot_net.backend.db import engine_options


def test_postgres_sessions_are_pinned_to_utc():
    opts = engine_options("postgresql+asyncpg://u:p@db/app")
    assert opts["connect_args"]["server_settings"]["timezone"] == "UTC"


def test_sqlite_needs_no_pin():
    """SQLite's CURRENT_TIMESTAMP is already UTC."""
    assert engine_options("sqlite+aiosqlite:///:memory:") == {}


def test_migration_0021_lists_every_naive_now_filled_column():
    """Every naive column Postgres fills with now() held server-local time
    before the UTC pin; the migration must convert all of them."""
    import importlib.util
    from pathlib import Path

    from sqlalchemy import DateTime

    from not_dot_net.backend.db import Base
    from not_dot_net.backend.models import register_all_models

    register_all_models()
    path = Path(__file__).parent.parent / "alembic/versions/0021_naive_timestamps_to_utc.py"
    spec = importlib.util.spec_from_file_location("m0021", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    now_filled = {
        (table.name, col.name)
        for table in Base.metadata.tables.values()
        for col in table.columns
        if isinstance(col.type, DateTime) and not col.type.timezone
        and col.server_default is not None and "now" in str(col.server_default.arg).lower()
    }
    assert now_filled == set(module.NOW_FILLED_COLUMNS)
