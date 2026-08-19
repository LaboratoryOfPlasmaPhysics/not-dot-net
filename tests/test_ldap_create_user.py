import pytest
from ldap3 import MODIFY_REPLACE, MODIFY_ADD, MODIFY_DELETE


class _Result(dict):
    def __init__(self):
        super().__init__({"description": "success", "message": ""})


class _FakeEntry:
    def __init__(self, attrs):
        from types import SimpleNamespace
        for k, v in attrs.items():
            setattr(self, k, SimpleNamespace(value=v))
        self.entry_dn = attrs.get("_dn", "CN=fake,DC=x")


class _FakeConn:
    def __init__(self, search_returns_entries=None, add_ok=True, modify_ok=True):
        self.search_returns = list(search_returns_entries or [])
        self.entries = []
        self.calls = []  # list of (op_name, args)
        self.add_ok = add_ok
        self.modify_ok = modify_ok
        self.result = _Result()
        self.bound = True

    def search(self, *args, **kwargs):
        self.calls.append(("search", (args, kwargs)))
        self.entries = self.search_returns
        return bool(self.search_returns)

    def add(self, dn, object_class, attributes):
        self.calls.append(("add", (dn, object_class, attributes)))
        self.result = _Result()
        if not self.add_ok:
            self.result["description"] = "alreadyExists"
            self.result["message"] = "exists"
        return self.add_ok

    def modify(self, dn, changes):
        self.calls.append(("modify", (dn, changes)))
        self.result = _Result()
        if not self.modify_ok:
            self.result["description"] = "constraintViolation"
            self.result["message"] = "nope"
        return self.modify_ok

    def unbind(self):
        self.bound = False


def _fake_connect_returning(conn):
    def _connect(cfg, username, password):
        return conn
    return _connect


def _new_user_kwargs(**overrides):
    base = dict(
        sam_account="alice",
        given_name="Alice",
        surname="Smith",
        display_name="Alice Smith",
        mail="alice.smith@example.com",
        description="newcomer",
        ou_dn="OU=Users,DC=x,DC=y",
        uid_number=10000,
        gid_number=10000,
        login_shell="/bin/bash",
        home_directory="/home/alice",
        initial_password="Init!Pass1234",
        must_change_password=True,
    )
    base.update(overrides)
    return base


def test_ldap_create_user_happy_path():
    from not_dot_net.backend.auth.ldap import ldap_create_user, NewAdUser, LdapConfig
    conn = _FakeConn()
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    new_user = NewAdUser(**_new_user_kwargs())

    dn = ldap_create_user(new_user, "admin", "pw", cfg, _fake_connect_returning(conn))
    assert dn == "CN=Alice Smith,OU=Users,DC=x,DC=y"

    ops = [c[0] for c in conn.calls]
    # Expected order: add → modify (password) → modify (pwdLastSet=0) → modify (UAC=0x200)
    assert ops == ["add", "modify", "modify", "modify"]

    _, (added_dn, oc, attrs) = conn.calls[0]
    assert added_dn == dn
    assert set(["top", "person", "organizationalPerson", "user"]).issubset(set(oc))
    assert attrs["sAMAccountName"] == "alice"
    assert attrs["uidNumber"] == 10000
    assert attrs["gidNumber"] == 10000
    assert attrs["loginShell"] == "/bin/bash"
    assert attrs["unixHomeDirectory"] == "/home/alice"
    assert attrs["mail"] == "alice.smith@example.com"
    assert attrs["description"] == "newcomer"
    assert attrs["userAccountControl"] == "514"  # 0x202

    # Password is UTF-16LE quoted
    _, (_, pwd_changes) = conn.calls[1]
    pwd_value = pwd_changes["unicodePwd"][0][1][0]
    assert pwd_value == ('"Init!Pass1234"').encode("utf-16-le")

    # pwdLastSet=0
    _, (_, pls_changes) = conn.calls[2]
    assert pls_changes["pwdLastSet"][0][1] == ["0"]

    # Final UAC enable
    _, (_, uac_changes) = conn.calls[3]
    assert uac_changes["userAccountControl"][0][1] == ["512"]  # 0x200


def test_ldap_create_user_escapes_rdn_special_chars():
    """A legitimate display name containing a comma must not break the DN
    (unescaped, 'Diaz, PhD' nests the object in a nonexistent sub-container)."""
    from not_dot_net.backend.auth.ldap import ldap_create_user, NewAdUser, LdapConfig

    conn = _FakeConn()
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    new_user = NewAdUser(**_new_user_kwargs(display_name="Diaz, PhD"))

    dn = ldap_create_user(new_user, "admin", "pw", cfg, _fake_connect_returning(conn))
    assert dn == "CN=Diaz\\, PhD,OU=Users,DC=x,DC=y"


def test_ldap_create_user_add_failure_raises_before_password():
    from not_dot_net.backend.auth.ldap import (
        ldap_create_user, NewAdUser, LdapConfig, LdapModifyError,
    )
    conn = _FakeConn(add_ok=False)
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    with pytest.raises(LdapModifyError):
        ldap_create_user(NewAdUser(**_new_user_kwargs()), "a", "p", cfg, _fake_connect_returning(conn))
    assert [c[0] for c in conn.calls] == ["add"]


def test_ldap_create_user_password_failure_raises():
    from not_dot_net.backend.auth.ldap import (
        ldap_create_user, NewAdUser, LdapConfig, LdapModifyError,
    )
    conn = _FakeConn(modify_ok=False)
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    with pytest.raises(LdapModifyError):
        ldap_create_user(NewAdUser(**_new_user_kwargs()), "a", "p", cfg, _fake_connect_returning(conn))
    assert [c[0] for c in conn.calls] == ["add", "modify"]  # add ok, password modify fails


def test_ldap_create_user_no_force_change_skips_pwdlastset():
    from not_dot_net.backend.auth.ldap import ldap_create_user, NewAdUser, LdapConfig
    conn = _FakeConn()
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    new_user = NewAdUser(**_new_user_kwargs(must_change_password=False))
    ldap_create_user(new_user, "a", "p", cfg, _fake_connect_returning(conn))
    ops = [c[0] for c in conn.calls]
    # add → unicodePwd modify → UAC enable (no pwdLastSet)
    assert ops == ["add", "modify", "modify"]


def test_ldap_add_to_groups_calls_modify_per_group():
    from not_dot_net.backend.auth.ldap import ldap_add_to_groups, LdapConfig
    conn = _FakeConn()
    cfg = LdapConfig(base_dn="DC=x")
    failures = ldap_add_to_groups(
        "CN=alice,DC=x",
        ["CN=g1,DC=x", "CN=g2,DC=x"],
        "admin", "pw", cfg, _fake_connect_returning(conn),
    )
    assert failures == {}
    assert [c[0] for c in conn.calls] == ["modify", "modify"]
    # Each modify uses MODIFY_ADD on member with the user DN
    for op, (gdn, changes) in conn.calls:
        assert "member" in changes
        action, value = changes["member"][0]
        assert action == MODIFY_ADD
        assert value == ["CN=alice,DC=x"]


def test_ldap_add_to_groups_collects_per_group_failures():
    from not_dot_net.backend.auth.ldap import ldap_add_to_groups, LdapConfig
    conn = _FakeConn(modify_ok=False)
    cfg = LdapConfig(base_dn="DC=x")
    failures = ldap_add_to_groups(
        "CN=alice,DC=x",
        ["CN=g1,DC=x"],
        "admin", "pw", cfg, _fake_connect_returning(conn),
    )
    assert "CN=g1,DC=x" in failures
    assert "constraintViolation" in failures["CN=g1,DC=x"] or "nope" in failures["CN=g1,DC=x"]


def test_ldap_remove_from_groups_uses_modify_delete():
    from not_dot_net.backend.auth.ldap import ldap_remove_from_groups, LdapConfig
    conn = _FakeConn()
    cfg = LdapConfig(base_dn="DC=x")
    ldap_remove_from_groups(
        "CN=alice,DC=x", ["CN=g1,DC=x"], "admin", "pw", cfg, _fake_connect_returning(conn),
    )
    op, (gdn, changes) = conn.calls[0]
    action, value = changes["member"][0]
    assert action == MODIFY_DELETE


def test_ldap_list_groups_returns_summaries():
    from not_dot_net.backend.auth.ldap import ldap_list_groups, LdapConfig
    entries = [
        _FakeEntry({"cn": "g1", "description": "team", "_dn": "CN=g1,OU=Groups,DC=x"}),
        _FakeEntry({"cn": "g2", "description": None, "_dn": "CN=g2,OU=Groups,DC=x"}),
    ]
    conn = _FakeConn(search_returns_entries=entries)
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    groups = ldap_list_groups("admin", "pw", cfg, connect=_fake_connect_returning(conn))
    assert len(groups) == 2
    dns = {g.dn for g in groups}
    assert dns == {"CN=g1,OU=Groups,DC=x", "CN=g2,OU=Groups,DC=x"}


def test_ldap_reset_password_happy_path():
    from not_dot_net.backend.auth.ldap import ldap_reset_password, LdapConfig
    conn = _FakeConn()
    cfg = LdapConfig(base_dn="DC=x,DC=y")

    ldap_reset_password("CN=alice,DC=x,DC=y", "Fresh!Pass987", "admin", "pw",
                        cfg, _fake_connect_returning(conn))

    ops = [c[0] for c in conn.calls]
    assert ops == ["modify", "modify"]  # unicodePwd, then pwdLastSet=0
    _, (_, pwd_changes) = conn.calls[0]
    assert pwd_changes["unicodePwd"][0][1][0] == ('"Fresh!Pass987"').encode("utf-16-le")
    _, (_, pls_changes) = conn.calls[1]
    assert pls_changes["pwdLastSet"][0][1] == ["0"]
    assert conn.bound is False  # unbound in finally


def test_ldap_reset_password_no_force_change_skips_pwdlastset():
    from not_dot_net.backend.auth.ldap import ldap_reset_password, LdapConfig
    conn = _FakeConn()
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    ldap_reset_password("CN=alice,DC=x", "Fresh!Pass987", "a", "p", cfg,
                        _fake_connect_returning(conn), must_change_password=False)
    assert [c[0] for c in conn.calls] == ["modify"]


def test_ldap_reset_password_failure_raises():
    from not_dot_net.backend.auth.ldap import ldap_reset_password, LdapConfig, LdapModifyError
    conn = _FakeConn(modify_ok=False)
    cfg = LdapConfig(base_dn="DC=x,DC=y")
    with pytest.raises(LdapModifyError):
        ldap_reset_password("CN=alice,DC=x", "Fresh!Pass987", "a", "p", cfg,
                            _fake_connect_returning(conn))
    assert [c[0] for c in conn.calls] == ["modify"]
