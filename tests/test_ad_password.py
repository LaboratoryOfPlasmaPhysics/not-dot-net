"""Self-service AD password change: LDAP for a valid password, Kerberos when expired."""

import pytest
from ldap3.core.exceptions import LDAPBindError

from not_dot_net.backend.auth import ad_password
from not_dot_net.backend.auth.ad_password import (
    BindFailure,
    PasswordChangeError,
    bind_failure,
    change_own_password,
)
from not_dot_net.backend.auth.ldap import LdapConfig
from not_dot_net.backend.auth.login_throttle import MAX_FAILURES

CFG = LdapConfig(url="ldaps://dc1.lpp.fr", domain="lpp.fr", base_dn="DC=lpp,DC=fr")
AD_ERR = "80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data {}, v4563"


@pytest.fixture(autouse=True)
def _clear_throttle():
    from not_dot_net.backend.auth import login_throttle
    login_throttle.reset()
    yield
    login_throttle.reset()


@pytest.mark.parametrize("code, expected", [
    ("773", BindFailure.MUST_CHANGE),
    ("532", BindFailure.EXPIRED),
    ("775", BindFailure.LOCKED),
    ("533", BindFailure.DISABLED),
    ("52e", BindFailure.BAD_CREDENTIALS),
])
def test_bind_failure_reads_ad_subcode(code, expected):
    assert bind_failure(LDAPBindError(AD_ERR.format(code))) == expected


def test_bind_failure_unknown_message():
    assert bind_failure(LDAPBindError("automatic bind not successful")) is None


class _Microsoft:
    def __init__(self, conn):
        self.conn = conn

    def modify_password(self, dn, new, old):
        self.conn.calls.append(("modify_password", dn, new, old))
        if not self.conn.modify_ok:
            self.conn.result = {"description": "constraintViolation",
                                "message": "0000052D: Constraint violation"}
        return self.conn.modify_ok


class _Entry:
    entry_dn = "CN=Alice,OU=Users,DC=lpp,DC=fr"


class _FakeConn:
    def __init__(self, modify_ok=True):
        self.modify_ok = modify_ok
        self.calls = []
        self.entries = []
        self.result = {}
        self.bound = True
        self.extend = type("E", (), {})()
        self.extend.microsoft = _Microsoft(self)

    def search(self, base, flt, **kw):
        self.calls.append(("search", flt))
        self.entries = [_Entry()]
        return True

    def unbind(self):
        self.bound = False


def _connect_ok(conn):
    def connect(cfg, username, password):
        conn.calls.append(("bind", username, password))
        return conn
    return connect


def _connect_fails(code):
    def connect(cfg, username, password):
        raise LDAPBindError(AD_ERR.format(code))
    return connect


def _no_kpasswd(*a, **kw):
    raise AssertionError("Kerberos must not be used when LDAP works")


def test_change_with_valid_password_uses_ldap_as_the_user():
    conn = _FakeConn()
    change_own_password("alice", "Old!Pass1", "New!Pass2", CFG,
                        connect=_connect_ok(conn), kpasswd=_no_kpasswd)
    assert ("bind", "alice", "Old!Pass1") in conn.calls
    assert ("modify_password", _Entry.entry_dn, "New!Pass2", "Old!Pass1") in conn.calls
    assert conn.bound is False


def test_change_rejected_by_policy():
    conn = _FakeConn(modify_ok=False)
    with pytest.raises(PasswordChangeError) as err:
        change_own_password("alice", "Old!Pass1", "short", CFG,
                            connect=_connect_ok(conn), kpasswd=_no_kpasswd)
    assert err.value.reason == "policy"
    assert conn.bound is False


def test_wrong_current_password():
    with pytest.raises(PasswordChangeError) as err:
        change_own_password("alice", "wrong", "New!Pass2", CFG,
                            connect=_connect_fails("52e"), kpasswd=_no_kpasswd)
    assert err.value.reason == "bad_credentials"


@pytest.mark.parametrize("code", ["773", "532"])
def test_expired_password_changes_through_kerberos(code):
    calls = []

    def kpasswd(username, domain, new, old, kdc_host):
        calls.append((username, domain, new, old, kdc_host))

    change_own_password("alice", "Temp!Pass1", "New!Pass2", CFG,
                        connect=_connect_fails(code), kpasswd=kpasswd)
    assert calls == [("alice", "lpp.fr", "New!Pass2", "Temp!Pass1", "dc1.lpp.fr")]


def test_expired_password_rejected_by_policy_through_kerberos():
    from impacket.krb5.kpasswd import KPasswdError

    def kpasswd(*a):
        raise KPasswdError("KRB5_KPASSWD_SOFTERROR: password does not meet requirements")

    with pytest.raises(PasswordChangeError) as err:
        change_own_password("alice", "Temp!Pass1", "x", CFG,
                            connect=_connect_fails("773"), kpasswd=kpasswd)
    assert err.value.reason == "policy"


def test_expired_password_kerberos_unreachable():
    def kpasswd(*a):
        raise OSError("connection refused")

    with pytest.raises(PasswordChangeError) as err:
        change_own_password("alice", "Temp!Pass1", "New!Pass2", CFG,
                            connect=_connect_fails("773"), kpasswd=kpasswd)
    assert err.value.reason == "unavailable"


def test_locked_account():
    with pytest.raises(PasswordChangeError) as err:
        change_own_password("alice", "Old!Pass1", "New!Pass2", CFG,
                            connect=_connect_fails("775"), kpasswd=_no_kpasswd)
    assert err.value.reason == "locked"


def test_invalid_username_never_reaches_ad():
    def connect(*a):
        raise AssertionError("must not bind")

    with pytest.raises(PasswordChangeError) as err:
        change_own_password("bob)(cn=*", "x", "y", CFG, connect=connect, kpasswd=_no_kpasswd)
    assert err.value.reason == "bad_credentials"


def test_repeated_wrong_passwords_are_throttled():
    for _ in range(MAX_FAILURES):
        with pytest.raises(PasswordChangeError):
            change_own_password("alice", "wrong", "New!Pass2", CFG,
                                connect=_connect_fails("52e"), kpasswd=_no_kpasswd)

    def connect(*a):
        raise AssertionError("throttled attempts must not reach AD")

    with pytest.raises(PasswordChangeError) as err:
        change_own_password("alice", "wrong", "New!Pass2", CFG, connect=connect, kpasswd=_no_kpasswd)
    assert err.value.reason == "throttled"


def test_default_connect_keeps_the_ad_subcode(monkeypatch):
    """ldap3's auto_bind drops AD's `data 773` detail; the login needs it."""
    from not_dot_net.backend.auth import ldap

    class _Conn:
        def __init__(self, *a, **kw):
            self.result = {"description": "invalidCredentials", "message": AD_ERR.format("773")}

        def open(self):
            pass

        def start_tls(self):
            pass

        def bind(self):
            return False

        def unbind(self):
            pass

    monkeypatch.setattr(ldap, "Connection", _Conn)
    monkeypatch.setattr(ldap, "_build_server_or_pool", lambda cfg: object())
    with pytest.raises(LDAPBindError) as err:
        ldap.default_ldap_connect(CFG, "alice", "Temp!Pass1")
    assert bind_failure(err.value) == BindFailure.MUST_CHANGE


def test_ldap_authenticate_reports_expired_password():
    from not_dot_net.backend.auth.ldap import AdPasswordExpired, ldap_authenticate
    with pytest.raises(AdPasswordExpired):
        ldap_authenticate("alice", "Temp!Pass1", CFG, _connect_fails("773"))


def test_ldap_authenticate_wrong_password_still_returns_none():
    from not_dot_net.backend.auth.ldap import ldap_authenticate
    assert ldap_authenticate("alice", "wrong", CFG, _connect_fails("52e")) is None


def test_admin_reset_can_unlock():
    from not_dot_net.backend.auth.ldap import ldap_reset_password
    conn = _FakeConn()
    modifies = []
    conn.modify = lambda dn, changes: modifies.append(changes) or True
    ldap_reset_password("CN=alice,DC=lpp,DC=fr", "Fresh!Pass987", "admin", "pw", CFG,
                        _connect_ok(conn), unlock=True)
    assert {"lockoutTime"} <= {k for m in modifies for k in m}


def test_kpasswd_targets_the_configured_dc():
    assert ad_password.kdc_host(CFG) == "dc1.lpp.fr"
    assert ad_password.kdc_host(LdapConfig(url="", domain="lpp.fr")) == "lpp.fr"


def test_ldap_server_has_a_connect_timeout():
    """A DC that drops packets must not freeze the login for the OS TCP timeout."""
    from not_dot_net.backend.auth.ldap import _build_server_or_pool
    server = _build_server_or_pool(CFG)
    assert server.connect_timeout and server.connect_timeout <= 15
