"""AD bulk sync matches people by sAMAccountName and retires vanished accounts.

Review 2026-10-12 F8: the sync matched by email only, so an AD mail change
provisioned a second account; users deleted from AD stayed active forever.
"""

import pytest
from sqlalchemy import select

from not_dot_net.backend.auth import ldap as ldap_module
from not_dot_net.backend.auth.ldap import LdapConfig, LdapUserInfo, ldap_config
from not_dot_net.backend.db import AuthMethod, User, session_scope


def _info(sam: str, email: str) -> LdapUserInfo:
    return LdapUserInfo(
        email=email, dn=f"cn={sam},ou=users,dc=example,dc=com",
        sam_account_name=sam, full_name=sam.title(), is_active=True,
    )


@pytest.fixture
def ad_returns(monkeypatch):
    async def _set(*infos: LdapUserInfo):
        await ldap_config.set(LdapConfig(url="fake", domain="example.com", base_dn="dc=example,dc=com"))
        entries = list(infos)
        monkeypatch.setattr(ldap_module, "_search_all_user_entries", lambda *a, **k: entries)
        monkeypatch.setattr(ldap_module, "_entry_to_user_info", lambda e, **k: e)
    return _set


async def _add(email: str, **fields) -> None:
    async with session_scope() as session:
        session.add(User(email=email, hashed_password="x", is_active=True, role="", **fields))
        await session.commit()


async def _users() -> dict[str, User]:
    async with session_scope() as session:
        return {u.email: u for u in (await session.execute(select(User))).scalars()}


async def test_mail_change_updates_the_same_account(ad_returns):
    await _add("jdupont@example.com", auth_method=AuthMethod.LDAP, ldap_username="jdupont")
    await ad_returns(_info("jdupont", "jeanne.martin@example.com"))

    result = await ldap_module.sync_all_from_ldap("admin", "pw")

    assert (result.synced, result.provisioned) == (1, 0)
    assert set(await _users()) == {"jeanne.martin@example.com"}


async def test_account_gone_from_ad_is_deactivated(ad_returns):
    await _add("alice@example.com", auth_method=AuthMethod.LDAP, ldap_username="alice")
    await _add("gone@example.com", auth_method=AuthMethod.LDAP, ldap_username="gone")
    await _add("local@example.com", auth_method=AuthMethod.LOCAL)
    await _add("root@example.com", auth_method=AuthMethod.LDAP, ldap_username="root", is_superuser=True)
    await ad_returns(_info("alice", "alice@example.com"))

    result = await ldap_module.sync_all_from_ldap("admin", "pw")

    users = await _users()
    assert result.deactivated == 1
    assert users["gone@example.com"].is_active is False
    assert users["alice@example.com"].is_active is True
    assert users["local@example.com"].is_active is True, "local accounts are not AD's to retire"
    assert users["root@example.com"].is_active is True, "never lock out a superuser automatically"


async def test_empty_ad_result_deactivates_nobody(ad_returns):
    """An empty search is far likelier a misconfigured base DN than an empty lab."""
    await _add("alice@example.com", auth_method=AuthMethod.LDAP, ldap_username="alice")
    await ad_returns()

    result = await ldap_module.sync_all_from_ldap("admin", "pw")

    assert result.deactivated == 0
    assert (await _users())["alice@example.com"].is_active is True
