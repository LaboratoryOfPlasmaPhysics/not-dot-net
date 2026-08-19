"""P3 — ConfigSection.get() must not hit the DB on every call.

87 call sites, and the worst amplifier is has_permissions -> roles_config.get()
on every permission check, which the 60s dashboard badge timer runs for every
connected client.

The cache holds the raw JSON, not the validated model: several callers mutate
what get() returns in place (admin_roles `cfg.roles[k] = ...`,
admin_email_templates `cfg.layout = ...`), so handing out a shared instance
would leak one admin's un-saved edits to every other reader.
"""
import pytest
from pydantic import BaseModel

from not_dot_net.backend import app_config
from not_dot_net.backend.app_config import section


class _Demo(BaseModel):
    name: str = "default"
    items: list[str] = []


@pytest.fixture
def counting_reads(monkeypatch):
    """Count how many times get() opens a DB session."""
    calls = {"n": 0}
    real = app_config.session_scope

    def counted(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(app_config, "session_scope", counted)
    return calls


async def test_repeated_get_reads_the_db_once(counting_reads):
    cfg = section("demo_cache", _Demo)
    await cfg.get()
    first = counting_reads["n"]
    assert first >= 1

    for _ in range(5):
        await cfg.get()
    assert counting_reads["n"] == first, "get() re-queried inside the TTL"


async def test_set_is_visible_immediately(counting_reads):
    cfg = section("demo_invalidate", _Demo)
    assert (await cfg.get()).name == "default"

    await cfg.set(_Demo(name="changed"))
    assert (await cfg.get()).name == "changed", "stale value served after set()"


async def test_get_returns_an_independent_object():
    """Mutating the result must not poison what the next caller sees."""
    cfg = section("demo_isolation", _Demo)
    await cfg.set(_Demo(name="stored", items=["a"]))

    first = await cfg.get()
    first.name = "mutated"
    first.items.append("b")

    second = await cfg.get()
    assert second.name == "stored"
    assert second.items == ["a"]


async def test_expired_entry_is_refetched(counting_reads, monkeypatch):
    cfg = section("demo_ttl", _Demo)
    await cfg.get()
    before = counting_reads["n"]

    monkeypatch.setattr(app_config, "CACHE_TTL_S", 0.0)
    await cfg.get()
    assert counting_reads["n"] > before, "expired entry was not refetched"
