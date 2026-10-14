"""Password change through the intranet: login redirect, change page, IT reset."""

import sys
import uuid

from ldap3.core.exceptions import LDAPBindError
from nicegui import ui
from nicegui.testing import User

from not_dot_net.backend.auth.ad_password import PasswordChangeError
from not_dot_net.backend.auth.ldap import set_ldap_connect, get_ldap_connect
from not_dot_net.backend.db import AuthMethod, User as UserModel

AD_773 = "80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data 773, v4563"


def _page_module():
    return sys.modules["not_dot_net.frontend.change_password"]


async def test_login_with_expired_ad_password_goes_to_change_page(user: User):
    def connect(cfg, username, password):
        raise LDAPBindError(AD_773)

    previous = get_ldap_connect()
    set_ldap_connect(connect)
    try:
        response = await user.http_client.post(
            "/auth/login",
            data={"username": "alice", "password": "Temp!Pass1"},
            follow_redirects=False,
        )
    finally:
        set_ldap_connect(previous)
    assert response.status_code == 303
    assert response.headers["location"] == "/change-password?username=alice&expired=1"


async def test_login_page_confirms_a_changed_password(user: User):
    await user.open("/login?changed=1")
    await user.should_see("Password changed")


async def _fill_change_form(user: User, *, new: str, confirm: str):
    user.find(marker="cp-current").type("Temp!Pass1")
    user.find(marker="cp-new").type(new)
    user.find(marker="cp-confirm").type(confirm)
    user.find(marker="cp-submit").click()


async def test_change_page_changes_the_password_then_back_to_login(user: User, monkeypatch):
    calls = []

    def fake_change(username, old, new, cfg):
        calls.append((username, old, new))

    await user.open("/change-password?username=alice&expired=1")
    monkeypatch.setattr(_page_module(), "change_own_password", fake_change)
    await user.should_see("Your password has expired")
    await _fill_change_form(user, new="New!Pass22", confirm="New!Pass22")
    await user.should_see("Password changed")
    assert calls == [("alice", "Temp!Pass1", "New!Pass22")]


async def test_change_page_refuses_mismatched_confirmation(user: User, monkeypatch):
    def fake_change(*a):
        raise AssertionError("must not reach AD")

    await user.open("/change-password?username=alice")
    monkeypatch.setattr(_page_module(), "change_own_password", fake_change)
    await _fill_change_form(user, new="New!Pass22", confirm="Other!Pass3")
    await user.should_see("do not match")


async def test_change_page_explains_a_policy_refusal(user: User, monkeypatch):
    def fake_change(*a):
        raise PasswordChangeError("policy")

    await user.open("/change-password?username=alice")
    monkeypatch.setattr(_page_module(), "change_own_password", fake_change)
    await _fill_change_form(user, new="short", confirm="short")
    await user.should_see("password rules")


async def test_change_page_gives_up_on_an_unreachable_server(user: User, monkeypatch):
    """A firewalled DC drops packets: without a limit the button spins for minutes."""
    import time

    def hanging_change(*a):
        time.sleep(2)

    await user.open("/change-password?username=alice")
    monkeypatch.setattr(_page_module(), "change_own_password", hanging_change)
    monkeypatch.setattr(_page_module(), "CHANGE_TIMEOUT_S", 0.2)
    await _fill_change_form(user, new="New!Pass22", confirm="New!Pass22")
    await user.should_see("can't be reached", retries=30)


def _people():
    superuser = UserModel(
        id=uuid.uuid4(), email="root-pw@test", hashed_password="x", is_active=True,
        is_superuser=True, full_name="Root", role="admin",
    )
    ad_target = UserModel(
        id=uuid.uuid4(), email="carol@example.com", hashed_password="!ldap-no-local-password",
        is_active=True, full_name="Carol", auth_method=AuthMethod.LDAP,
        ldap_dn="cn=carol,dc=example,dc=com",
    )
    return superuser, ad_target


async def test_reset_button_visible_for_superuser_on_ad_user(user: User):
    from not_dot_net.frontend.directory import _render_detail

    superuser, ad_target = _people()

    @ui.page("/_reset_button_super")
    async def _page():
        container = ui.column()
        with container:
            await _render_detail(container, ad_target, superuser, state={})

    await user.open("/_reset_button_super")
    await user.should_see("Reset password")


async def test_reset_button_hidden_for_non_superuser(user: User):
    from not_dot_net.frontend.directory import _render_detail

    superuser, ad_target = _people()
    superuser.is_superuser = False

    @ui.page("/_reset_button_admin")
    async def _page():
        container = ui.column()
        with container:
            await _render_detail(container, ad_target, superuser, state={})

    await user.open("/_reset_button_admin")
    await user.should_not_see("Reset password")


async def test_reset_writes_ad_unlocks_and_shows_the_password_once(user: User, monkeypatch):
    from not_dot_net.frontend.directory import _render_reset_password_button

    superuser, ad_target = _people()
    calls = []

    def fake_reset(dn, new_password, bind_username, bind_password, ldap_cfg, **kw):
        calls.append((dn, new_password, bind_username, kw))

    monkeypatch.setitem(_render_reset_password_button.__globals__, "ldap_reset_password", fake_reset)
    monkeypatch.setitem(
        _render_reset_password_button.__globals__, "generate_initial_password", lambda n: "Gen!Pass12345"
    )

    async def fake_prompt(current_user, on_bind):
        await on_bind("it-admin", "admin-pw")

    monkeypatch.setitem(_render_reset_password_button.__globals__, "prompt_ad_credentials", fake_prompt)

    @ui.page("/_reset_flow")
    async def _page():
        _render_reset_password_button(ad_target, superuser)

    await user.open("/_reset_flow")
    user.find(marker="reset-open").click()
    user.find(marker="reset-confirm").click()
    await user.should_see("Gen!Pass12345")
    assert calls == [("cn=carol,dc=example,dc=com", "Gen!Pass12345", "it-admin",
                      {"must_change_password": True, "unlock": True})]


def test_it_reset_is_a_sensitive_audit_event():
    from types import SimpleNamespace
    from not_dot_net.frontend.audit_log import _audit_severity

    ev = SimpleNamespace(category="user", action="reset_password", metadata_json={}, detail="")
    assert _audit_severity(ev) == "orange"


async def _log_in_as(user: User, email: str, *, ldap_username: str | None) -> None:
    import asyncio
    from contextlib import asynccontextmanager

    from not_dot_net.backend.db import get_user_db, session_scope
    from not_dot_net.backend.schemas import UserCreate
    from not_dot_net.backend.users import get_jwt_strategy, get_user_manager

    await user.open("/login")
    await asyncio.sleep(0.2)  # let startup tasks finish
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                db_user = await manager.create(UserCreate(email=email, password="pw123456"))
        if ldap_username:
            db_user = await session.get(UserModel, db_user.id)
            db_user.auth_method = AuthMethod.LDAP
            db_user.ldap_username = ldap_username
            await session.commit()
    token = await get_jwt_strategy().write_token(db_user)
    user.http_client.cookies.set("fastapiusersauth", token)


async def test_account_menu_offers_password_change_to_ad_users(user: User):
    await _log_in_as(user, "dave@test.com", ldap_username="dave")
    await user.open("/")
    await user.should_see("Change my password")


async def test_account_menu_hides_password_change_for_local_users(user: User):
    await _log_in_as(user, "erin@test.com", ldap_username=None)
    await user.open("/")
    await user.should_see("Dashboard")
    await user.should_not_see("Change my password")
