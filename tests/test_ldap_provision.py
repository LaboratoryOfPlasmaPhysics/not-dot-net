"""Tests for LDAP auto-provisioning and login integration."""

import pytest
from contextlib import asynccontextmanager

from not_dot_net.backend.auth.ldap import (
    LdapConfig, LdapUserInfo, provision_ldap_user, ldap_config,
    set_ldap_connect, ldap_authenticate,
)
from not_dot_net.backend.db import AuthMethod, User, get_user_db, session_scope
from not_dot_net.backend.users import get_user_manager
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.frontend.login import _try_ldap_auth

from ldap3 import Server, Connection, MOCK_SYNC, OFFLINE_AD_2012_R2
from ldap3.core.exceptions import LDAPBindError


LDAP_CFG = LdapConfig(
    url="fake", domain="example.com", base_dn="dc=example,dc=com",
    auto_provision=True,
)


def _make_fake_connect(users: dict):
    """Build a fake LDAP connect function with the given user data."""
    def fake_connect(ldap_cfg, username, password):
        server = Server("fake_ad", get_info=OFFLINE_AD_2012_R2)
        conn = Connection(server, user=f"{username}@{ldap_cfg.domain}",
                          password=password, client_strategy=MOCK_SYNC)
        for uid, attrs in users.items():
            entry_attrs = {
                "sAMAccountName": uid,
                "userPassword": attrs["password"],
                "objectClass": "person",
            }
            for attr in ("mail", "displayName", "givenName", "sn",
                         "telephoneNumber", "physicalDeliveryOfficeName", "title", "department"):
                if attrs.get(attr):
                    entry_attrs[attr] = attrs[attr]
            conn.strategy.add_entry(f"cn={uid},ou=users,{ldap_cfg.base_dn}", entry_attrs)
        conn.bind()
        if users.get(username, {}).get("password") != password:
            raise LDAPBindError("Invalid credentials")
        return conn
    return fake_connect


async def test_provision_ldap_user_creates_user():
    info = LdapUserInfo(
        email="ad.user@example.com",
        dn="cn=ad.user,dc=example,dc=com",
        full_name="AD User",
        given_name="AD",
        surname="User",
    )
    user = await provision_ldap_user(info, default_role="member")

    assert user.email == "ad.user@example.com"
    assert user.full_name == "AD User"
    assert user.auth_method == AuthMethod.LDAP
    assert user.role == "member"
    assert user.is_active is True


async def test_provision_sets_empty_role_when_no_default():
    info = LdapUserInfo(email="norole@example.com", dn="cn=norole,dc=example,dc=com", full_name="No Role")
    user = await provision_ldap_user(info, default_role="")
    assert user.role == ""


async def test_try_ldap_auth_provisions_new_user():
    """Full integration: _try_ldap_auth should provision a user on first LDAP login."""
    fake_users = {
        "newguy": {
            "mail": "newguy@example.com",
            "displayName": "New Guy",
            "givenName": "New",
            "sn": "Guy",
            "password": "pass123",
        },
    }
    # Set the LDAP config with auto_provision=True
    await ldap_config.set(LDAP_CFG)
    set_ldap_connect(_make_fake_connect(fake_users))

    user = await _try_ldap_auth("newguy", "pass123")
    assert user is not None
    assert user.email == "newguy@example.com"
    assert user.full_name == "New Guy"
    assert user.auth_method == AuthMethod.LDAP


async def test_try_ldap_auth_returns_existing_user():
    """If the user already exists locally, return them without re-provisioning."""
    # Create a local user first
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as mgr:
                user = await mgr.create(
                    UserCreate(email="existing@example.com", password="x", is_active=True)
                )
                original_id = user.id

    fake_users = {
        "existing": {
            "mail": "existing@example.com",
            "displayName": "Existing",
            "givenName": "Ex",
            "sn": "Isting",
            "password": "ldappass",
        },
    }
    await ldap_config.set(LDAP_CFG)
    set_ldap_connect(_make_fake_connect(fake_users))

    user = await _try_ldap_auth("existing", "ldappass")
    assert user is not None
    assert user.id == original_id


async def test_try_ldap_auth_bad_password_returns_none():
    fake_users = {"someone": {"mail": "s@example.com", "password": "right"}}
    await ldap_config.set(LDAP_CFG)
    set_ldap_connect(_make_fake_connect(fake_users))

    user = await _try_ldap_auth("someone", "wrong")
    assert user is None


async def test_try_ldap_auth_invalid_username_returns_none():
    """Usernames with special chars should be rejected."""
    user = await _try_ldap_auth("'; DROP TABLE users;--", "pass")
    assert user is None


async def test_try_ldap_auto_provision_off():
    """When auto_provision is False, unknown LDAP users should not be created."""
    cfg_no_auto = LdapConfig(
        url="fake", domain="example.com", base_dn="dc=example,dc=com",
        auto_provision=False,
    )
    fake_users = {
        "noprov": {
            "mail": "noprov@example.com",
            "displayName": "No Prov",
            "givenName": "No",
            "sn": "Prov",
            "password": "pass",
        },
    }
    await ldap_config.set(cfg_no_auto)
    set_ldap_connect(_make_fake_connect(fake_users))

    user = await _try_ldap_auth("noprov", "pass")
    assert user is None


async def test_provision_stores_dn():
    from not_dot_net.backend.auth.ldap import provision_ldap_user, LdapUserInfo
    info = LdapUserInfo(email="provision-dn@example.com", dn="cn=provision-dn,dc=example,dc=com")
    user = await provision_ldap_user(info, default_role="member")
    assert user.ldap_dn == "cn=provision-dn,dc=example,dc=com"


async def test_existing_ldap_user_gets_resynced_on_login():
    fake_users = {
        "jdoe": {
            "mail": "jdoe@example.com",
            "displayName": "John Doe",
            "givenName": "John", "sn": "Doe",
            "telephoneNumber": "+33NEW", "physicalDeliveryOfficeName": "Room 101",
            "title": "Researcher", "department": "Plasma",
            "password": "secret",
        },
    }
    await ldap_config.set(LDAP_CFG)
    set_ldap_connect(_make_fake_connect(fake_users))

    # Pre-seed a local user with stale data
    async with session_scope() as session:
        user = User(
            email="jdoe@example.com", hashed_password="x", is_active=True,
            auth_method=AuthMethod.LDAP, phone="+33OLD", office="Old Room",
            employment_status="Permanent",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    user = await _try_ldap_auth("jdoe", "secret")
    assert user is not None
    assert user.phone == "+33NEW"
    assert user.office == "Room 101"
    assert user.title == "Researcher"
    assert user.team == "Plasma"

    async with session_scope() as session:
        refreshed = await session.get(User, user_id)
        assert refreshed.employment_status == "Permanent"  # preserved
