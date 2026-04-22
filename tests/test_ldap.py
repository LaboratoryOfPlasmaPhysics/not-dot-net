import pytest
from ldap3 import Server, Connection, MOCK_SYNC, OFFLINE_AD_2012_R2

from not_dot_net.backend.auth.ldap import ldap_authenticate, LdapConfig, LdapUserInfo

LDAP_CFG = LdapConfig(url="fake", domain="example.com", base_dn="dc=example,dc=com")

FAKE_USERS = {
    "jdoe": {
        "mail": "jdoe@example.com",
        "displayName": "John Doe",
        "givenName": "John",
        "sn": "Doe",
        "telephoneNumber": "+33123456789",
        "physicalDeliveryOfficeName": "Room 101",
        "title": "Researcher",
        "department": "Plasma",
        "password": "secret",
    },
    "nomail": {"mail": None, "displayName": "No Mail", "givenName": "No", "sn": "Mail", "password": "secret"},
}


def fake_ldap_connect(ldap_cfg: LdapConfig, username: str, password: str) -> Connection:
    """Build a MOCK_SYNC connection pre-populated with fake AD entries."""
    server = Server("fake_ad", get_info=OFFLINE_AD_2012_R2)
    conn = Connection(server, user=f"{username}@{ldap_cfg.domain}", password=password, client_strategy=MOCK_SYNC)

    for uid, attrs in FAKE_USERS.items():
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

    if FAKE_USERS.get(username, {}).get("password") != password:
        from ldap3.core.exceptions import LDAPBindError
        raise LDAPBindError("Invalid credentials")

    return conn


def test_successful_authentication():
    result = ldap_authenticate("jdoe", "secret", LDAP_CFG, connect=fake_ldap_connect)
    assert result is not None
    assert result.email == "jdoe@example.com"
    assert result.full_name == "John Doe"
    assert result.given_name == "John"
    assert result.surname == "Doe"


def test_wrong_password_returns_none():
    result = ldap_authenticate("jdoe", "wrong", LDAP_CFG, connect=fake_ldap_connect)
    assert result is None


def test_unknown_user_returns_none():
    result = ldap_authenticate("nobody", "secret", LDAP_CFG, connect=fake_ldap_connect)
    assert result is None


def test_user_without_mail_returns_none():
    result = ldap_authenticate("nomail", "secret", LDAP_CFG, connect=fake_ldap_connect)
    assert result is None


class TestEffectiveUrl:
    def test_bare_hostname_gets_ldap_scheme(self):
        cfg = LdapConfig(url="dc01.example.com")
        assert cfg.effective_url == "ldap://dc01.example.com"

    def test_bare_hostname_gets_ldaps_when_tls_ldaps(self):
        from not_dot_net.backend.auth.ldap import TlsMode
        cfg = LdapConfig(url="dc01.example.com", tls_mode=TlsMode.LDAPS)
        assert cfg.effective_url == "ldaps://dc01.example.com"

    def test_full_url_unchanged(self):
        cfg = LdapConfig(url="ldap://dc01.example.com")
        assert cfg.effective_url == "ldap://dc01.example.com"

    def test_ldaps_url_unchanged(self):
        cfg = LdapConfig(url="ldaps://dc01.example.com")
        assert cfg.effective_url == "ldaps://dc01.example.com"

    def test_whitespace_stripped(self):
        cfg = LdapConfig(url="  dc01.example.com  ")
        assert cfg.effective_url == "ldap://dc01.example.com"


def test_authentication_returns_dn_and_extended_attrs():
    result = ldap_authenticate("jdoe", "secret", LDAP_CFG, connect=fake_ldap_connect)
    assert result is not None
    assert result.dn == "cn=jdoe,ou=users,dc=example,dc=com"
    assert result.phone == "+33123456789"
    assert result.office == "Room 101"
    assert result.title == "Researcher"
    assert result.department == "Plasma"
