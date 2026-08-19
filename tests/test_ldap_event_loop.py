"""B1/P1/P2 — synchronous ldap3 calls must not block the NiceGUI event loop.

ldap3 is a blocking library and NiceGUI serves every client's websocket from a
single event loop, so an AD call reached from an async handler has to go through
`asyncio.to_thread` (the convention already used in workflow_service.py and
uid_allocator.py). One slow domain controller otherwise freezes every client.
"""
import asyncio
import time

import pytest

from not_dot_net.backend.auth.ldap import LdapConfig, ldap_config, set_ldap_connect
from tests.test_ldap_provision import _make_fake_connect

BLOCK_SECONDS = 0.3
TICK = 0.01


async def _ticks_during(coro) -> int:
    """Run `coro` while a 10 ms ticker runs; return how many ticks got through.

    A blocking call inside the loop starves the ticker; a to_thread'd one doesn't.
    """
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(TICK)
            ticks += 1

    tick_task = asyncio.create_task(ticker())
    try:
        return_value = await coro
    finally:
        tick_task.cancel()
    return ticks, return_value


def _slow_connect(users: dict):
    """A fake AD whose every connect takes BLOCK_SECONDS, like an unreachable DC."""
    inner = _make_fake_connect(users)

    def connect(ldap_cfg, username, password):
        time.sleep(BLOCK_SECONDS)
        return inner(ldap_cfg, username, password)

    return connect


AD_USERS = {
    "slowuser": {"mail": "slowuser@example.com", "displayName": "Slow User", "password": "pw"},
    "admin": {"mail": "admin@example.com", "displayName": "Admin", "password": "adminpw"},
}


@pytest.fixture
async def slow_ad():
    cfg = LdapConfig(url="fake", domain="example.com",
                     base_dn="dc=example,dc=com", auto_provision=True)
    await ldap_config.set(cfg)
    set_ldap_connect(_slow_connect(AD_USERS))
    return cfg


async def test_login_ldap_fallback_does_not_block_event_loop(slow_ad):
    """The unauthenticated POST /auth/login path — worst case, anyone can trigger it."""
    from not_dot_net.frontend.login import _try_ldap_auth

    ticks, user = await _ticks_during(_try_ldap_auth("slowuser", "pw"))
    assert user is not None
    assert ticks > 5, f"event loop stalled during LDAP auth (only {ticks} ticks)"


async def test_bulk_ad_state_does_not_block_event_loop(slow_ad, monkeypatch):
    """A 3-user bulk enable/disable must not stall the loop for 3 x BLOCK_SECONDS.

    `apply_bulk_ad_state` calls `ldap_set_account_enabled` without threading the
    injected connect function through, so the slow-AD fixture can't reach it —
    patch the call site directly instead of letting it hit the real network.
    """
    from not_dot_net.backend.db import AuthMethod, User, session_scope
    from not_dot_net.frontend import user_management
    from not_dot_net.frontend.user_management import apply_bulk_ad_state

    monkeypatch.setattr(
        user_management, "ldap_set_account_enabled",
        lambda **kwargs: time.sleep(BLOCK_SECONDS),
    )

    async with session_scope() as session:
        actor = User(email="actor@example.com", hashed_password="x", is_active=True, role="")
        targets = [
            User(
                email=f"bulk{i}@example.com", hashed_password="x", is_active=True, role="",
                auth_method=AuthMethod.LDAP, ldap_dn=f"cn=bulk{i},ou=users,dc=example,dc=com",
            )
            for i in range(3)
        ]
        session.add_all([actor, *targets])
        await session.commit()
        for u in (actor, *targets):
            await session.refresh(u)
        actor_id, target_ids = actor.id, [t.id for t in targets]

    async with session_scope() as session:
        actor = await session.get(User, actor_id)
        targets = [await session.get(User, tid) for tid in target_ids]

    ticks, result = await _ticks_during(apply_bulk_ad_state(
        targets, enabling=False, bind_username="admin",
        bind_password="adminpw", actor=actor,
    ))
    assert len(result.succeeded) == 3
    assert ticks > 5, f"event loop stalled during bulk AD op (only {ticks} ticks)"
