"""A4: seed_fake_users must refuse to run when DATABASE_URL is set
(i.e., not dev mode). Otherwise --seed-fake-users on a production deploy
would create ~100 users with password "dev"."""

import os

import pytest

from not_dot_net.backend.seeding import seed_fake_users


async def test_seed_fake_users_refuses_in_production(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://prod-host/prod-db")
    with pytest.raises(RuntimeError, match=r"(?i)dev|production"):
        await seed_fake_users()


def test_guard_passes_when_database_url_unset(monkeypatch):
    """Non-regression: the prod guard alone must not block dev seeding."""
    from not_dot_net.backend.seeding import _refuse_in_production
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _refuse_in_production()  # no exception
