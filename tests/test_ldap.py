import pytest
from ldap3.core.exceptions import LDAPException
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
    "upnonly": {"mail": None, "userPrincipalName": "upnonly@example.com", "displayName": "UPN Only", "password": "secret"},
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
                     "telephoneNumber", "physicalDeliveryOfficeName", "title", "department",
                     "userPrincipalName", "sAMAccountName"):
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
    info, conn = result
    assert info.email == "jdoe@example.com"
    assert info.full_name == "John Doe"
    assert info.given_name == "John"
    assert info.surname == "Doe"


def test_wrong_password_returns_none():
    result = ldap_authenticate("jdoe", "wrong", LDAP_CFG, connect=fake_ldap_connect)
    assert result is None


def test_unknown_user_returns_none():
    result = ldap_authenticate("nobody", "secret", LDAP_CFG, connect=fake_ldap_connect)
    assert result is None


def test_user_without_mail_falls_back_to_domain():
    result = ldap_authenticate("nomail", "secret", LDAP_CFG, connect=fake_ldap_connect)
    assert result is not None
    info, _ = result
    assert info.email == "nomail@example.com"


def test_user_without_mail_falls_back_to_upn():
    result = ldap_authenticate("upnonly", "secret", LDAP_CFG, connect=fake_ldap_connect)
    assert result is not None
    info, _ = result
    assert info.email == "upnonly@example.com"


class TestEffectiveUrls:
    def test_bare_hostname_gets_ldap_scheme(self):
        cfg = LdapConfig(url="dc01.example.com")
        assert cfg.effective_urls == ["ldap://dc01.example.com"]

    def test_bare_hostname_gets_ldaps_when_tls_ldaps(self):
        from not_dot_net.backend.auth.ldap import TlsMode
        cfg = LdapConfig(url="dc01.example.com", tls_mode=TlsMode.LDAPS)
        assert cfg.effective_urls == ["ldaps://dc01.example.com"]

    def test_full_url_unchanged(self):
        cfg = LdapConfig(url="ldap://dc01.example.com")
        assert cfg.effective_urls == ["ldap://dc01.example.com"]

    def test_ldaps_url_unchanged(self):
        cfg = LdapConfig(url="ldaps://dc01.example.com")
        assert cfg.effective_urls == ["ldaps://dc01.example.com"]

    def test_whitespace_stripped(self):
        cfg = LdapConfig(url="  dc01.example.com  ")
        assert cfg.effective_urls == ["ldap://dc01.example.com"]

    def test_multiple_urls_comma_separated(self):
        cfg = LdapConfig(url="dc01.example.com, dc02.example.com")
        assert cfg.effective_urls == ["ldap://dc01.example.com", "ldap://dc02.example.com"]

    def test_multiple_urls_mixed_schemes(self):
        cfg = LdapConfig(url="ldap://dc01.example.com, dc02.example.com")
        assert cfg.effective_urls == ["ldap://dc01.example.com", "ldap://dc02.example.com"]

    def test_effective_url_returns_first(self):
        cfg = LdapConfig(url="dc01.example.com, dc02.example.com")
        assert cfg.effective_url == "ldap://dc01.example.com"

    def test_empty_url_triggers_dns_discovery(self):
        from unittest.mock import patch, MagicMock
        from not_dot_net.backend.auth.ldap import TlsMode

        mock_record = MagicMock()
        mock_record.target.to_text.return_value = "dc01.corp.local."
        mock_record.priority = 0
        mock_record.weight = 100
        mock_record2 = MagicMock()
        mock_record2.target.to_text.return_value = "dc02.corp.local."
        mock_record2.priority = 10
        mock_record2.weight = 50

        with patch("dns.resolver.resolve", return_value=[mock_record2, mock_record]):
            cfg = LdapConfig(url="", domain="corp.local")
            urls = cfg.effective_urls
        assert urls == ["ldap://dc01.corp.local", "ldap://dc02.corp.local"]

    def test_empty_url_ldaps_uses_ldaps_srv(self):
        from unittest.mock import patch, MagicMock, call
        from not_dot_net.backend.auth.ldap import TlsMode

        mock_record = MagicMock()
        mock_record.target.to_text.return_value = "dc01.corp.local."
        mock_record.priority = 0
        mock_record.weight = 100

        with patch("dns.resolver.resolve", return_value=[mock_record]) as mock_resolve:
            cfg = LdapConfig(url="", domain="corp.local", tls_mode=TlsMode.LDAPS)
            urls = cfg.effective_urls
        mock_resolve.assert_called_once_with("_ldaps._tcp.corp.local", "SRV")
        assert urls == ["ldaps://dc01.corp.local"]

    def test_empty_url_dns_failure_falls_back_to_domain(self):
        from unittest.mock import patch
        import dns.resolver

        with patch("dns.resolver.resolve", side_effect=dns.resolver.NXDOMAIN):
            cfg = LdapConfig(url="", domain="corp.local")
            assert cfg.effective_urls == ["ldap://corp.local"]


def test_authentication_returns_dn_and_extended_attrs():
    result = ldap_authenticate("jdoe", "secret", LDAP_CFG, connect=fake_ldap_connect)
    assert result is not None
    info, _ = result
    assert info.dn == "cn=jdoe,ou=users,dc=example,dc=com"
    assert info.phone == "+33123456789"
    assert info.office == "Room 101"
    assert info.title == "Researcher"
    assert info.department == "Plasma"


def test_ldap_exception_returns_none():
    def raising_connect(ldap_cfg: LdapConfig, username: str, password: str):
        raise LDAPException("server unavailable")

    result = ldap_authenticate("jdoe", "secret", LDAP_CFG, connect=raising_connect)
    assert result is None


def test_search_filter_includes_escaped_username():
    captured: dict[str, object] = {}

    class FakeConnection:
        def __init__(self):
            self.entries = []

        def search(self, base_dn, search_filter, attributes):
            captured["base_dn"] = base_dn
            captured["search_filter"] = search_filter
            captured["attributes"] = attributes
            self.entries = []

        def unbind(self):
            captured["unbound"] = True

    def connect(ldap_cfg: LdapConfig, username: str, password: str):
        captured["username"] = username
        return FakeConnection()

    result = ldap_authenticate("admin)(objectClass=*", "secret", LDAP_CFG, connect=connect)

    assert result is None
    assert captured["base_dn"] == LDAP_CFG.base_dn
    assert captured["search_filter"] == r"(sAMAccountName=admin\29\28objectClass=\2a)"
    assert captured["unbound"] is True


def test_search_filter_includes_configured_user_filter():
    captured: dict[str, object] = {}
    ldap_cfg = LdapConfig(
        url="fake",
        domain="example.com",
        base_dn="dc=example,dc=com",
        user_filter="(memberOf=cn=intranet,ou=groups,dc=example,dc=com)",
    )

    class FakeConnection:
        def __init__(self):
            self.entries = []

        def search(self, base_dn, search_filter, attributes):
            captured["search_filter"] = search_filter
            self.entries = []

        def unbind(self):
            captured["unbound"] = True

    def connect(ldap_cfg: LdapConfig, username: str, password: str):
        return FakeConnection()

    result = ldap_authenticate("jdoe", "secret", ldap_cfg, connect=connect)

    assert result is None
    assert captured["search_filter"] == (
        "(&(sAMAccountName=jdoe)"
        "(memberOf=cn=intranet,ou=groups,dc=example,dc=com))"
    )
    assert captured["unbound"] is True


def test_connection_remains_bound_after_success():
    state = {"unbound": False}

    class Attr:
        def __init__(self, value):
            self.value = value

    class Entry:
        entry_dn = "cn=jdoe,ou=users,dc=example,dc=com"
        mail = Attr("jdoe@example.com")
        displayName = Attr("John Doe")
        givenName = Attr("John")
        sn = Attr("Doe")
        telephoneNumber = Attr("+33123456789")
        physicalDeliveryOfficeName = Attr("Room 101")
        title = Attr("Researcher")
        department = Attr("Plasma")

    class FakeConnection:
        def __init__(self):
            self.entries = [Entry()]

        def search(self, base_dn, search_filter, attributes):
            return True

        def unbind(self):
            state["unbound"] = True

    def connect(ldap_cfg: LdapConfig, username: str, password: str):
        return FakeConnection()

    result = ldap_authenticate("jdoe", "secret", LDAP_CFG, connect=connect)

    assert result is not None
    assert state["unbound"] is False


def test_connection_unbound_after_user_not_found():
    state = {"unbound": False}

    class FakeConnection:
        def __init__(self):
            self.entries = []

        def search(self, base_dn, search_filter, attributes):
            self.entries = []
            return True

        def unbind(self):
            state["unbound"] = True

    def connect(ldap_cfg: LdapConfig, username: str, password: str):
        return FakeConnection()

    result = ldap_authenticate("missing", "secret", LDAP_CFG, connect=connect)

    assert result is None
    assert state["unbound"] is True


def test_connection_remains_bound_after_missing_mail_fallback():
    state = {"unbound": False}

    class Attr:
        def __init__(self, value):
            self.value = value

    class Entry:
        entry_dn = "cn=nomail,ou=users,dc=example,dc=com"
        mail = Attr(None)
        userPrincipalName = Attr(None)
        displayName = Attr("No Mail")
        givenName = Attr("No")
        sn = Attr("Mail")

    class FakeConnection:
        def __init__(self):
            self.entries = [Entry()]

        def search(self, base_dn, search_filter, attributes):
            return True

        def unbind(self):
            state["unbound"] = True

    def connect(ldap_cfg: LdapConfig, username: str, password: str):
        return FakeConnection()

    result = ldap_authenticate("nomail", "secret", LDAP_CFG, connect=connect)

    assert result is not None
    info, _ = result
    assert info.email == "nomail@example.com"
    assert state["unbound"] is False
