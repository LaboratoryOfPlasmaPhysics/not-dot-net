"""Tests for the superuser-only user management tab."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from not_dot_net.backend.db import AuthMethod, User, session_scope
from not_dot_net.frontend import user_management as um
from not_dot_net.frontend.user_management import (
    UserFilter, apply_bulk_ad_state, filter_users,
)


def test_columns_have_no_static_format_string():
    """Quasar calls column.format as a function; a Python string would throw a JS
    TypeError on every cell and cause the entire table to render no rows.
    Dynamic JS bindings must use the ":format" prefix in NiceGUI, or the column
    must drop format altogether.
    """
    for col in um._COLUMNS:
        if "format" in col:
            value = col["format"]
            assert callable(value) or not isinstance(value, str), (
                f"Column {col['name']!r} has a static string 'format' which Quasar "
                f"will try to call as a function; rename to ':format' or remove."
            )


def _u(**kw) -> SimpleNamespace:
    """Build a User-shaped object for filter tests (avoids hitting DB)."""
    defaults = dict(
        full_name="Alice Smith", email="alice@example.com", ldap_username="alice",
        auth_method=AuthMethod.LDAP, role="member",
        is_active=True, is_superuser=False,
        employment_status="Permanent", last_ad_logon=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


_NOW = datetime(2026, 5, 1, tzinfo=timezone.utc)


def test_filter_query_matches_full_name():
    users = [
        _u(full_name="Alice", email="a@x", ldap_username="a"),
        _u(full_name="Bob", email="b@x", ldap_username="b"),
    ]
    out = filter_users(users, UserFilter(query="ali"), now=_NOW)
    assert [u.full_name for u in out] == ["Alice"]


def test_filter_query_matches_email_substring():
    users = [_u(email="alice@x.com"), _u(email="bob@y.com")]
    out = filter_users(users, UserFilter(query="bob@"), now=_NOW)
    assert [u.email for u in out] == ["bob@y.com"]


def test_filter_query_matches_ldap_username():
    users = [_u(full_name=None, email="x@x", ldap_username="jsmith")]
    out = filter_users(users, UserFilter(query="jsmith"), now=_NOW)
    assert len(out) == 1


def test_filter_auth_method():
    users = [
        _u(email="local@x", auth_method=AuthMethod.LOCAL),
        _u(email="ad@x", auth_method=AuthMethod.LDAP),
    ]
    assert [u.email for u in filter_users(users, UserFilter(auth_method="local"), now=_NOW)] == ["local@x"]
    assert [u.email for u in filter_users(users, UserFilter(auth_method="ldap"), now=_NOW)] == ["ad@x"]


def test_filter_active_state():
    users = [_u(email="on@x", is_active=True), _u(email="off@x", is_active=False)]
    assert [u.email for u in filter_users(users, UserFilter(active="active"), now=_NOW)] == ["on@x"]
    assert [u.email for u in filter_users(users, UserFilter(active="inactive"), now=_NOW)] == ["off@x"]


def test_filter_logon_never():
    users = [
        _u(email="ever@x", last_ad_logon=_NOW - timedelta(days=10)),
        _u(email="never@x", last_ad_logon=None),
    ]
    out = filter_users(users, UserFilter(last_logon="never"), now=_NOW)
    assert [u.email for u in out] == ["never@x"]


def test_filter_logon_threshold_treats_no_logon_as_stale():
    users = [
        _u(email="recent@x", last_ad_logon=_NOW - timedelta(days=5)),
        _u(email="old@x", last_ad_logon=_NOW - timedelta(days=200)),
        _u(email="never@x", last_ad_logon=None),
    ]
    out = filter_users(users, UserFilter(last_logon="180d"), now=_NOW)
    assert sorted(u.email for u in out) == ["never@x", "old@x"]


def test_filter_logon_handles_naive_datetime():
    """SQLite may return naive datetimes; filter must coerce to UTC."""
    naive_old = (_NOW - timedelta(days=200)).replace(tzinfo=None)
    users = [_u(email="naive@x", last_ad_logon=naive_old)]
    out = filter_users(users, UserFilter(last_logon="90d"), now=_NOW)
    assert len(out) == 1


def test_filter_combined():
    users = [
        _u(email="match@x", auth_method=AuthMethod.LDAP, is_active=False,
           last_ad_logon=_NOW - timedelta(days=400)),
        _u(email="local_recent@x", auth_method=AuthMethod.LOCAL, is_active=False,
           last_ad_logon=None),
    ]
    out = filter_users(
        users,
        UserFilter(auth_method="ldap", active="inactive", last_logon="1y"),
        now=_NOW,
    )
    assert [u.email for u in out] == ["match@x"]


# --- apply_bulk_ad_state ---

async def _persist_user(**kw) -> User:
    async with session_scope() as session:
        user = User(
            email=kw.pop("email"),
            hashed_password="x",
            is_active=kw.pop("is_active", True),
            auth_method=kw.pop("auth_method", AuthMethod.LDAP),
            ldap_dn=kw.pop("ldap_dn", "cn=u,dc=example,dc=com"),
            **kw,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_bulk_disable_succeeds_and_mirrors_local(monkeypatch):
    calls = []

    def fake_set_account_enabled(*, dn, enabled, **_kw):
        calls.append((dn, enabled))

    monkeypatch.setattr(um, "ldap_set_account_enabled", fake_set_account_enabled)

    actor = await _persist_user(email="actor@x", is_superuser=True,
                                auth_method=AuthMethod.LOCAL, ldap_dn=None)
    u1 = await _persist_user(email="u1@x", ldap_dn="cn=u1,dc=example,dc=com", is_active=True)
    u2 = await _persist_user(email="u2@x", ldap_dn="cn=u2,dc=example,dc=com", is_active=True)

    result = await apply_bulk_ad_state(
        [u1, u2], enabling=False,
        bind_username="admin", bind_password="pw", actor=actor,
    )

    assert len(result.succeeded) == 2
    assert result.failed == []
    assert {dn for dn, _ in calls} == {"cn=u1,dc=example,dc=com", "cn=u2,dc=example,dc=com"}
    assert all(enabled is False for _, enabled in calls)

    async with session_scope() as session:
        for u in (u1, u2):
            refreshed = await session.get(User, u.id)
            assert refreshed.is_active is False


async def test_bulk_skips_self(monkeypatch):
    monkeypatch.setattr(um, "ldap_set_account_enabled", lambda **_kw: None)
    actor = await _persist_user(email="self@x", is_superuser=True,
                                ldap_dn="cn=self,dc=example,dc=com")
    result = await apply_bulk_ad_state(
        [actor], enabling=False,
        bind_username="admin", bind_password="pw", actor=actor,
    )
    assert result.succeeded == []
    assert len(result.failed) == 1
    assert result.failed[0][1] == "self"


async def test_bulk_skips_non_ad_user(monkeypatch):
    monkeypatch.setattr(um, "ldap_set_account_enabled", lambda **_kw: None)
    actor = await _persist_user(email="actor2@x", is_superuser=True)
    local = await _persist_user(email="local@x", auth_method=AuthMethod.LOCAL, ldap_dn=None)
    result = await apply_bulk_ad_state(
        [local], enabling=False,
        bind_username="admin", bind_password="pw", actor=actor,
    )
    assert result.succeeded == []
    assert result.failed == [(local, "not_ad")]


async def test_bulk_collects_per_user_failures(monkeypatch):
    from not_dot_net.backend.auth.ldap import LdapModifyError

    def flaky(*, dn, enabled, **_kw):
        if "u2" in dn:
            raise LdapModifyError("modify failed")

    monkeypatch.setattr(um, "ldap_set_account_enabled", flaky)
    actor = await _persist_user(email="actor3@x", is_superuser=True)
    u1 = await _persist_user(email="ok@x", ldap_dn="cn=u1,dc=example,dc=com")
    u2 = await _persist_user(email="ko@x", ldap_dn="cn=u2,dc=example,dc=com")
    result = await apply_bulk_ad_state(
        [u1, u2], enabling=True,
        bind_username="admin", bind_password="pw", actor=actor,
    )
    assert [u.email for u in result.succeeded] == ["ok@x"]
    assert [(p.email, "modify" in r) for p, r in result.failed] == [("ko@x", True)]


# --- Review 2026-10-12 U7/U8: filters below 50 rows, every filter change
# rebuilt the table (losing the selection), edit only by double-click.


async def _render_user_management(user, path: str):
    from nicegui import ui
    from not_dot_net.backend.db import User, session_scope
    from not_dot_net.frontend.user_management import render

    async with session_scope() as session:
        root = User(email="root-um@test.com", hashed_password="x", is_active=True, is_superuser=True)
        session.add_all([
            root,
            User(email="alice-um@test.com", full_name="Alice", hashed_password="x", is_active=True),
            User(email="bob-um@test.com", full_name="Bob", hashed_password="x", is_active=True),
        ])
        await session.commit()
        await session.refresh(root)

    @ui.page(path)
    async def _page():
        await render(root)

    await user.open(path)


async def test_filtering_keeps_the_table_and_its_selection(user):
    from nicegui import ui
    await _render_user_management(user, "/_um_filter")
    table = next(iter(user.find(kind=ui.table).elements))
    alice = next(r for r in table.rows if r["email"] == "alice-um@test.com")
    table.selected = [alice]

    user.find(marker="user-search").type("alice")

    assert next(iter(user.find(kind=ui.table).elements)) is table
    assert [r["email"] for r in table.rows] == ["alice-um@test.com"]
    assert table.selected == [alice]


async def test_rows_have_an_edit_button(user):
    from nicegui import ui
    await _render_user_management(user, "/_um_edit")
    table = next(iter(user.find(kind=ui.table).elements))
    assert "body-cell-actions" in table.slots


async def test_bulk_buttons_sit_above_the_table(user):
    """I6: with 50 rows per page they were a full scroll below the selection."""
    from nicegui import ui
    from tests.ui_order import drawn_before

    await _render_user_management(user, "/_um_bulk_top")
    table = next(iter(user.find(kind=ui.table).elements))
    button = next(iter(user.find("Disable in AD").elements))
    assert drawn_before(button, table)
