import pytest
from ldap3 import Server, Connection, MOCK_SYNC, OFFLINE_AD_2012_R2
from ldap3.core.exceptions import LDAPBindError

from not_dot_net.backend.auth.ldap import (
    LdapConfig, ldap_modify_user, LdapModifyError,
)

LDAP_CFG = LdapConfig(url="fake", domain="example.com", base_dn="dc=example,dc=com")
USER_DN = "cn=jdoe,ou=users,dc=example,dc=com"


def _make_mutable_fake(initial_attrs: dict):
    """Shared-state fake connect. Modifies write into the returned dict."""
    state = dict(initial_attrs)

    def fake_connect(ldap_cfg, username, password):
        server = Server("fake_ad", get_info=OFFLINE_AD_2012_R2)
        conn = Connection(server, user=f"{username}@{ldap_cfg.domain}",
                          password=password, client_strategy=MOCK_SYNC)
        conn.strategy.add_entry(USER_DN, {
            "sAMAccountName": "jdoe", "userPassword": "secret",
            "objectClass": "person", "mail": "jdoe@example.com",
            **{k: v for k, v in state.items() if v is not None},
        })
        conn.bind()
        if password != "secret":
            raise LDAPBindError("Invalid credentials")
        orig_modify = conn.modify
        def tracked_modify(dn, changes, *a, **kw):
            result = orig_modify(dn, changes, *a, **kw)
            for attr, ops in changes.items():
                _op, values = ops[0]
                state[attr] = values[0] if values else None
            return result
        conn.modify = tracked_modify
        return conn

    return fake_connect, state


def test_modify_writes_changes():
    fake_connect, state = _make_mutable_fake({
        "telephoneNumber": "+33111", "physicalDeliveryOfficeName": "Old",
    })
    ldap_modify_user(
        dn=USER_DN,
        changes={"telephoneNumber": "+33999", "physicalDeliveryOfficeName": "New Room"},
        bind_username="jdoe", bind_password="secret",
        ldap_cfg=LDAP_CFG, connect=fake_connect,
    )
    assert state["telephoneNumber"] == "+33999"
    assert state["physicalDeliveryOfficeName"] == "New Room"


def test_modify_bind_failure_raises():
    fake_connect, _ = _make_mutable_fake({"telephoneNumber": "+33111"})
    with pytest.raises(LdapModifyError) as exc:
        ldap_modify_user(
            dn=USER_DN, changes={"telephoneNumber": "x"},
            bind_username="jdoe", bind_password="wrong",
            ldap_cfg=LDAP_CFG, connect=fake_connect,
        )
    assert "bind" in str(exc.value).lower()


def test_modify_empty_changes_is_noop():
    fake_connect, state = _make_mutable_fake({"telephoneNumber": "+33111"})
    ldap_modify_user(
        dn=USER_DN, changes={},
        bind_username="jdoe", bind_password="secret",
        ldap_cfg=LDAP_CFG, connect=fake_connect,
    )
    assert state == {"telephoneNumber": "+33111"}


def test_modify_clears_attribute_when_value_is_none():
    fake_connect, state = _make_mutable_fake({"physicalDeliveryOfficeName": "Old"})
    ldap_modify_user(
        dn=USER_DN, changes={"physicalDeliveryOfficeName": None},
        bind_username="jdoe", bind_password="secret",
        ldap_cfg=LDAP_CFG, connect=fake_connect,
    )
    assert state["physicalDeliveryOfficeName"] is None
