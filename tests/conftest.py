import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from not_dot_net.backend.db import Base
import not_dot_net.backend.db as db_module
from not_dot_net.backend.secrets import AppSecrets
from not_dot_net.backend.users import init_user_secrets


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    # Match production PostgreSQL semantics so FK violations surface in tests.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture()
async def mock_ad_effects(monkeypatch):
    """Monkeypatch AD effect handlers to avoid requiring real LDAP credentials in tests."""
    from not_dot_net.backend.workflow_effects import EFFECT_REGISTRY, EffectResult
    
    async def fake_run(request, step, action, params, ad_creds, actor):
        return EffectResult(kind="ad_add_to_groups", succeeded=True)
    
    monkeypatch.setattr(EFFECT_REGISTRY["ad_add_to_groups"], "run", fake_run)


@pytest.fixture(autouse=True)
async def setup_db(request, monkeypatch):
    """Set up an in-memory SQLite DB and dev secrets for each test."""
    init_user_secrets(AppSecrets(jwt_secret="test-secret-that-is-long-enough-for-hs256", storage_secret="test-storage", file_encryption_key="test-file-encryption-key-32bytes!"))

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    old_engine, old_session = db_module._engine, db_module._async_session_maker
    db_module._engine = engine
    db_module._async_session_maker = session_maker

    # The NiceGUI user fixture executes not_dot_net/app.py main(): its
    # init_db() would rebind the engine to ./dev.db mid-test, and its dev
    # startup work (create_all, default-admin seeding, outbox worker) runs
    # as NiceGUI background tasks racing the test body and teardown
    # (refresh on a disposed engine, concurrent commits on the shared
    # in-memory SQLite connection). Patch it all out — but only for tests
    # that use the user fixture; other tests call these functions directly
    # and need the real ones. The user fixture runs app.py via runpy
    # (__mp_main__), which re-imports names at exec time, so the patches
    # must go on the SOURCE modules.
    if "user" in request.fixturenames:
        import not_dot_net.app as app_module
        import not_dot_net.backend.mail_outbox as mail_outbox_module
        import not_dot_net.backend.users as users_module

        async def _noop_create_tables():
            return None

        async def _noop_default_admin(email, password):
            return None

        async def _noop_worker():
            return None

        monkeypatch.setattr(db_module, "init_db", lambda url: None)
        monkeypatch.setattr(app_module, "init_db", lambda url: None)
        monkeypatch.setattr(db_module, "create_db_and_tables", _noop_create_tables)
        monkeypatch.setattr(app_module, "create_db_and_tables", _noop_create_tables)
        monkeypatch.setattr(users_module, "ensure_default_admin", _noop_default_admin)
        monkeypatch.setattr(app_module, "ensure_default_admin", _noop_default_admin)
        monkeypatch.setattr(mail_outbox_module, "run_outbox_worker", _noop_worker)

        import not_dot_net.backend.vocabularies as vocabularies_module

        async def _noop_seed_vocabularies():
            return None

        monkeypatch.setattr(vocabularies_module, "ensure_vocabularies_seeded",
                            _noop_seed_vocabularies)

    import not_dot_net.backend.workflow_models  # noqa: F401
    import not_dot_net.backend.booking_models  # noqa: F401
    import not_dot_net.backend.floorplan_models  # noqa: F401
    import not_dot_net.backend.office_availability  # noqa: F401
    import not_dot_net.backend.audit  # noqa: F401
    import not_dot_net.backend.app_config  # noqa: F401
    import not_dot_net.backend.encrypted_storage  # noqa: F401
    import not_dot_net.backend.tenure_service  # noqa: F401
    import not_dot_net.backend.mail_outbox  # noqa: F401
    import not_dot_net.backend.uid_allocator  # noqa: F401
    import not_dot_net.backend.effect_retry  # noqa: F401

    # Each test gets a fresh in-memory DB, but the ConfigSection cache is module
    # state — without this a section read in one test is served to the next.
    from not_dot_net.backend.app_config import invalidate_config_cache
    invalidate_config_cache()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    invalidate_config_cache()
    await engine.dispose()
    db_module._engine, db_module._async_session_maker = old_engine, old_session
