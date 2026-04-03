import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from not_dot_net.backend.db import Base
import not_dot_net.backend.db as db_module


class SampleConfig(BaseModel):
    name: str = "default"
    count: int = 42
    tags: list[str] = ["a", "b"]


@pytest.fixture(autouse=True)
async def setup_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    old_engine, old_session = db_module._engine, db_module._async_session_maker
    db_module._engine = engine
    db_module._async_session_maker = session_maker
    import not_dot_net.backend.app_config  # noqa: F401 — register AppSetting model
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()
    db_module._engine, db_module._async_session_maker = old_engine, old_session


async def test_get_returns_defaults_when_no_db_row():
    from not_dot_net.backend.app_config import section
    cfg_section = section("test_default", SampleConfig)
    result = await cfg_section.get()
    assert result == SampleConfig()


async def test_set_then_get_roundtrips():
    from not_dot_net.backend.app_config import section
    cfg_section = section("test_roundtrip", SampleConfig)
    custom = SampleConfig(name="custom", count=99, tags=["x"])
    await cfg_section.set(custom)
    result = await cfg_section.get()
    assert result == custom


async def test_reset_reverts_to_defaults():
    from not_dot_net.backend.app_config import section
    cfg_section = section("test_reset", SampleConfig)
    await cfg_section.set(SampleConfig(name="changed"))
    await cfg_section.reset()
    result = await cfg_section.get()
    assert result == SampleConfig()


async def test_registry_tracks_sections():
    from not_dot_net.backend.app_config import section, get_registry
    cfg_section = section("test_registry", SampleConfig)
    registry = get_registry()
    assert "test_registry" in registry
    assert registry["test_registry"] is cfg_section
