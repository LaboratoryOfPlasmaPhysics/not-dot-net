import pytest
from httpx import ASGITransport, AsyncClient
from nicegui import app

from not_dot_net.backend.db import Base
from not_dot_net.backend.users import authenticate_and_get_token


@pytest.fixture(autouse=True)
async def fresh_db(tmp_path):
    """Override DB to use a fresh temp database for each test."""
    from not_dot_net.config import init_settings, _settings
    from not_dot_net.backend import db
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    if _settings is None:
        init_settings()

    old_engine, old_session = db._engine, db._async_session_maker
    db._engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    db._async_session_maker = async_sessionmaker(db._engine, expire_on_commit=False)
    import not_dot_net.backend.onboarding  # noqa: F401

    from not_dot_net.backend.onboarding_router import router as onboarding_router
    if not any(r is onboarding_router for r in app.routes):
        app.include_router(onboarding_router)

    async with db._engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from not_dot_net.backend.users import ensure_default_admin
    await ensure_default_admin()
    yield
    await db._engine.dispose()
    db._engine, db._async_session_maker = old_engine, old_session


async def _get_auth_header():
    token = await authenticate_and_get_token("admin@not-dot-net.dev", "admin")
    return {"Cookie": f"fastapiusersauth={token}"}


async def test_create_onboarding_request():
    headers = await _get_auth_header()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/onboarding",
            json={
                "person_name": "Jane Doe",
                "person_email": "jane@lpp.fr",
                "role_status": "PhD student",
                "team": "Plasma Physics",
                "start_date": "2026-09-01",
            },
            headers=headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["person_name"] == "Jane Doe"
        assert data["status"] == "pending"


async def test_list_onboarding_requests():
    headers = await _get_auth_header()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/onboarding",
            json={
                "person_name": "Jane Doe",
                "person_email": "jane@lpp.fr",
                "role_status": "PhD student",
                "team": "Plasma Physics",
                "start_date": "2026-09-01",
            },
            headers=headers,
        )
        resp = await client.get("/api/onboarding", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1
