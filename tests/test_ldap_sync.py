import pytest

from not_dot_net.backend.auth.ldap import LdapUserInfo, sync_user_from_ldap
from not_dot_net.backend.db import User, AuthMethod, session_scope


async def test_sync_updates_mapped_fields_only():
    async with session_scope() as session:
        user = User(
            email="old@example.com", hashed_password="x", is_active=True,
            auth_method=AuthMethod.LDAP, full_name="Old Name",
            phone="+33000", office="Old Office", title="Old Title", team="Old Team",
            employment_status="Permanent",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    info = LdapUserInfo(
        email="new@example.com", dn="cn=x,dc=example,dc=com",
        full_name="New Name", phone="+33111", office="New Office",
        title="New Title", department="New Team",
    )
    await sync_user_from_ldap(user_id, info)

    async with session_scope() as session:
        refreshed = await session.get(User, user_id)
        assert refreshed.email == "new@example.com"
        assert refreshed.full_name == "New Name"
        assert refreshed.phone == "+33111"
        assert refreshed.office == "New Office"
        assert refreshed.title == "New Title"
        assert refreshed.team == "New Team"
        assert refreshed.employment_status == "Permanent"
        assert refreshed.ldap_dn == "cn=x,dc=example,dc=com"


async def test_sync_accepts_none_values():
    async with session_scope() as session:
        user = User(
            email="u@example.com", hashed_password="x", is_active=True,
            auth_method=AuthMethod.LDAP, phone="+33111",
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    info = LdapUserInfo(email="u@example.com", dn="cn=x,dc=example,dc=com", phone=None)
    await sync_user_from_ldap(user_id, info)

    async with session_scope() as session:
        refreshed = await session.get(User, user_id)
        assert refreshed.phone is None
