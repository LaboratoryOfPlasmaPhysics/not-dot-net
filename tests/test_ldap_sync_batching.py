"""P7 — sync_all_from_ldap opened a session and committed per AD entry.

A 1000-user directory sync meant 1000 sessions and 1000 commits, and the
initial user load pulled the non-deferred LargeBinary photo column for everyone.
"""
import uuid

import pytest
from sqlalchemy import inspect as sa_inspect

from not_dot_net.backend.auth import ldap as ldap_module
from not_dot_net.backend.auth.ldap import LdapConfig, LdapUserInfo, ldap_config
from not_dot_net.backend.db import User, session_scope


def _info(n: int) -> LdapUserInfo:
    return LdapUserInfo(
        email=f"synced{n}@example.com",
        dn=f"cn=synced{n},ou=users,dc=example,dc=com",
        full_name=f"Synced {n}",
        is_active=True,
    )


@pytest.fixture
def count_sessions(monkeypatch):
    calls = {"n": 0}
    real = ldap_module.__dict__.get("session_scope")

    from not_dot_net.backend import db as db_module

    original = db_module.session_scope

    def counted(*a, **k):
        calls["n"] += 1
        return original(*a, **k)

    monkeypatch.setattr(db_module, "session_scope", counted)
    return calls


async def test_bulk_sync_uses_a_bounded_number_of_sessions(count_sessions, monkeypatch):
    await ldap_config.set(LdapConfig(
        url="fake", domain="example.com", base_dn="dc=example,dc=com",
    ))
    entries = [object() for _ in range(20)]
    monkeypatch.setattr(ldap_module, "_search_all_user_entries", lambda *a, **k: entries)
    infos = iter([_info(i) for i in range(20)])
    monkeypatch.setattr(ldap_module, "_entry_to_user_info", lambda e, **k: next(infos))

    count_sessions["n"] = 0
    result = await ldap_module.sync_all_from_ldap("admin", "pw")

    assert result.provisioned == 20
    assert count_sessions["n"] <= 5, (
        f"opened {count_sessions['n']} sessions for 20 entries"
    )


async def test_bulk_sync_still_provisions_and_updates():
    """Behaviour must be unchanged: new users created, existing ones updated."""
    async with session_scope() as session:
        session.add(User(
            email="synced0@example.com", hashed_password="x",
            is_active=True, role="", full_name="Stale Name",
        ))
        await session.commit()

    import not_dot_net.backend.auth.ldap as lm

    await ldap_config.set(LdapConfig(
        url="fake", domain="example.com", base_dn="dc=example,dc=com",
    ))
    entries = [object(), object()]
    infos = iter([_info(0), _info(1)])
    original_search = lm._search_all_user_entries
    original_convert = lm._entry_to_user_info
    lm._search_all_user_entries = lambda *a, **k: entries
    lm._entry_to_user_info = lambda e, **k: next(infos)
    try:
        result = await lm.sync_all_from_ldap("admin", "pw")
    finally:
        lm._search_all_user_entries = original_search
        lm._entry_to_user_info = original_convert

    assert result.synced == 1
    assert result.provisioned == 1

    async with session_scope() as session:
        from sqlalchemy import select
        updated = (await session.execute(
            select(User).where(User.email == "synced0@example.com")
        )).scalar_one()
        assert updated.full_name == "Synced 0", "existing user was not updated"


async def test_bulk_sync_does_not_load_photo_bytes(monkeypatch):
    await ldap_config.set(LdapConfig(
        url="fake", domain="example.com", base_dn="dc=example,dc=com",
    ))
    async with session_scope() as session:
        session.add(User(
            email="hasphoto@example.com", hashed_password="x", is_active=True,
            role="", photo=b"\xff\xd8\xff" + b"x" * 4096,
        ))
        await session.commit()

    seen = {}
    original = ldap_module._existing_users_by_email

    async def spy(session):
        users = await original(session)
        seen.update(users)
        return users

    monkeypatch.setattr(ldap_module, "_existing_users_by_email", spy)
    monkeypatch.setattr(ldap_module, "_search_all_user_entries", lambda *a, **k: [])

    await ldap_module.sync_all_from_ldap("admin", "pw")

    user = seen.get("hasphoto@example.com")
    assert user is not None
    assert "photo" in sa_inspect(user).unloaded, "bulk sync loaded photo bytes"
