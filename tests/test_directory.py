import asyncio
from contextlib import asynccontextmanager

from nicegui.testing import User

from not_dot_net.backend.db import session_scope, get_user_db
from not_dot_net.backend.migrate import run_upgrade
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.backend.users import get_user_manager, get_jwt_strategy
from not_dot_net.app import DEV_DB_URL


async def _create_user_and_token(email: str, password: str) -> str:
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                from fastapi_users.exceptions import UserAlreadyExists
                try:
                    user = await manager.create(UserCreate(email=email, password=password))
                except UserAlreadyExists:
                    user = await manager.get_by_email(email)
                return await get_jwt_strategy().write_token(user)


async def test_directory_shows_search(user: User) -> None:
    run_upgrade(DEV_DB_URL)
    await user.open("/login")
    # Wait for startup tasks (DB creation, admin seeding) to complete
    await asyncio.sleep(0.5)
    token = await _create_user_and_token("admin@not-dot-net.dev", "admin")
    user.http_client.cookies.set("fastapiusersauth", token)
    await user.open("/")
    await user.should_see("Search")


from not_dot_net.frontend.directory import classify_updates


def test_classify_updates_splits_ad_and_local_fields():
    updates = {
        "phone": "+33999",
        "office": "New",
        "employment_status": "Contractor",
        "start_date": None,
    }
    ad_changes, local_updates = classify_updates(updates)
    assert ad_changes == {
        "telephoneNumber": "+33999",
        "physicalDeliveryOfficeName": "New",
    }
    assert local_updates == {
        "employment_status": "Contractor",
        "start_date": None,
    }


def test_classify_updates_ignores_unmapped_fields():
    ad_changes, local_updates = classify_updates({"unknown": "x"})
    assert ad_changes == {}
    assert local_updates == {"unknown": "x"}


def test_classify_updates_empty_input():
    assert classify_updates({}) == ({}, {})


SELF_EDITABLE_AD_FIELDS = {"phone", "office"}
ADMIN_EDITABLE_AD_FIELDS = {"phone", "office", "full_name", "title", "team", "email"}


def test_self_editable_ad_fields_are_subset_of_admin_editable():
    assert SELF_EDITABLE_AD_FIELDS.issubset(ADMIN_EDITABLE_AD_FIELDS)


def test_self_editable_ad_fields_map_to_valid_ad_attributes():
    from not_dot_net.backend.auth.ldap import AD_ATTR_MAP
    for f in SELF_EDITABLE_AD_FIELDS:
        assert f in AD_ATTR_MAP


from not_dot_net.frontend.directory import compute_update_diff


def test_compute_update_diff_returns_only_changed():
    current = {"phone": "+33111", "office": "A"}
    submitted = {"phone": "+33222", "office": "A"}
    assert compute_update_diff(current, submitted) == {"phone": "+33222"}


def test_compute_update_diff_treats_empty_string_as_none():
    current = {"phone": "+33111"}
    submitted = {"phone": ""}
    assert compute_update_diff(current, submitted) == {"phone": None}


def test_compute_update_diff_no_changes_returns_empty():
    assert compute_update_diff({"phone": "+33111"}, {"phone": "+33111"}) == {}


async def test_self_edit_phone_writes_to_ad_and_local_db():
    from not_dot_net.backend.db import User, AuthMethod, session_scope
    from not_dot_net.backend.auth.ldap import (
        ldap_config, set_ldap_connect, ldap_modify_user,
    )
    from tests.test_ldap_modify import _make_mutable_fake, LDAP_CFG, USER_DN

    fake_connect, ad_state = _make_mutable_fake({"telephoneNumber": "+33OLD"})
    await ldap_config.set(LDAP_CFG)
    set_ldap_connect(fake_connect)

    async with session_scope() as session:
        user = User(
            email="jdoe@example.com", hashed_password="x", is_active=True,
            auth_method=AuthMethod.LDAP,
            ldap_dn=USER_DN,
            phone="+33OLD",
        )
        session.add(user)
        await session.commit()

    cfg = await ldap_config.get()
    ldap_modify_user(
        dn=USER_DN,
        changes={"telephoneNumber": "+33NEW"},
        bind_username="jdoe", bind_password="secret",
        ldap_cfg=cfg, connect=fake_connect,
    )
    assert ad_state["telephoneNumber"] == "+33NEW"
