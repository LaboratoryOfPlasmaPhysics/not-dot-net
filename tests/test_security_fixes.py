"""Reproducers for the 2026-07-02 whole-branch review security findings:
setup-wizard stale-tab superuser creation, spoofable X-Forwarded-For audit IP,
and the Socket.IO CORS lock that disabled origin checks instead of enforcing
same-origin."""

from types import SimpleNamespace

from not_dot_net.backend.db import User, session_scope


# --- setup wizard: a stale /setup tab must not create a second superuser ---


async def _superuser_emails() -> list[str]:
    from sqlalchemy import select

    async with session_scope() as session:
        result = await session.execute(select(User.email).where(User.is_superuser.is_(True)))
        return sorted(result.scalars().all())


async def test_complete_setup_refuses_when_superuser_exists():
    from not_dot_net.frontend.setup_wizard import complete_setup

    assert await complete_setup("first@test.dev", "pw12345678") is True
    assert await complete_setup("second@test.dev", "pw12345678") is False
    assert await _superuser_emails() == ["first@test.dev"]


async def test_complete_setup_creates_first_superuser():
    from not_dot_net.frontend.setup_wizard import complete_setup, has_superuser

    assert await has_superuser() is False
    assert await complete_setup("admin@test.dev", "pw12345678") is True
    assert await has_superuser() is True


# --- request_ip: X-Forwarded-For is client-suppliable; HAProxy APPENDS the
# real client IP, so only the last element is trustworthy ---


def _request(headers=None, client_host="192.0.2.9"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=client_host))


def test_request_ip_uses_last_forwarded_for_element():
    from not_dot_net.backend.audit import request_ip

    spoofed = _request({"x-forwarded-for": "10.0.0.5, 172.16.1.20"})
    assert request_ip(spoofed) == "172.16.1.20"


def test_request_ip_single_forwarded_for_element():
    from not_dot_net.backend.audit import request_ip

    assert request_ip(_request({"x-forwarded-for": "172.16.1.20"})) == "172.16.1.20"


def test_request_ip_falls_back_to_client_host():
    from not_dot_net.backend.audit import request_ip

    assert request_ip(_request()) == "192.0.2.9"
    assert request_ip(None) is None


# --- Socket.IO CORS: [] is engineio's "disable CORS handling entirely";
# same-origin enforcement needs None ---


def test_socketio_cors_defaults_to_same_origin(monkeypatch):
    from nicegui import core

    from not_dot_net.app import _lock_socketio_cors

    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    monkeypatch.setattr(
        core, "sio",
        SimpleNamespace(eio=SimpleNamespace(cors_allowed_origins="*")),
        raising=False,
    )
    _lock_socketio_cors()
    assert core.sio.eio.cors_allowed_origins is None


def test_socketio_cors_honors_explicit_allowlist(monkeypatch):
    from nicegui import core

    from not_dot_net.app import _lock_socketio_cors

    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example, https://b.example")
    monkeypatch.setattr(
        core, "sio",
        SimpleNamespace(eio=SimpleNamespace(cors_allowed_origins="*")),
        raising=False,
    )
    _lock_socketio_cors()
    assert core.sio.eio.cors_allowed_origins == ["https://a.example", "https://b.example"]
