from contextlib import asynccontextmanager

from nicegui.testing import User

from not_dot_net.backend.db import session_scope, get_user_db
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.backend.users import get_user_manager, get_jwt_strategy


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
    # NOTE: this test used to stamp_head(DEV_DB_URL) — writing alembic_version
    # to the developer's real dev.db on every run, which made `migrate` a
    # no-op there while the schema stayed stale. Never touch dev.db in tests.
    await user.open("/login")
    token = await _create_user_and_token("admin@not-dot-net.dev", "admin")
    user.http_client.cookies.set("fastapiusersauth", token)
    await user.open("/")
    await user.should_see("Search")


from not_dot_net.frontend.directory import classify_updates


async def test_update_user_cannot_set_is_superuser():
    """_update_user must never let a privileged field like is_superuser through.

    The directory edit form only submits profile-field keys today, but the update
    path went through UserManager.update with safe=False, so a stray is_superuser
    key would escalate. Harden the sink itself.
    """
    import uuid
    from not_dot_net.backend.db import User, session_scope
    from not_dot_net.frontend.directory import _update_user

    uid = uuid.uuid4()
    async with session_scope() as session:
        session.add(User(
            id=uid, email="victim@test.com", hashed_password="x",
            is_active=True, is_superuser=False, is_verified=True,
        ))
        await session.commit()

    await _update_user(uid, {"is_superuser": True, "title": "Engineer"})

    async with session_scope() as session:
        reloaded = await session.get(User, uid)
        assert reloaded.is_superuser is False
        assert reloaded.title == "Engineer"


async def test_load_people_returns_only_active_users():
    from not_dot_net.backend.db import User
    from not_dot_net.frontend.directory import _load_people

    async with session_scope() as session:
        active = User(
            email="active-directory@test.com",
            hashed_password="x",
            is_active=True,
            full_name="Active User",
        )
        inactive = User(
            email="inactive-directory@test.com",
            hashed_password="x",
            is_active=False,
            full_name="Inactive User",
        )
        session.add_all([active, inactive])
        await session.commit()

    people = await _load_people()
    emails = {person.email for person in people}
    assert "active-directory@test.com" in emails
    assert "inactive-directory@test.com" not in emails


async def test_load_people_sorted_by_display_name_case_insensitive():
    """Users should come back sorted by full_name (or email when full_name is empty),
    case-insensitively — matches the display order in the People tab.
    """
    from not_dot_net.backend.db import User
    from not_dot_net.frontend.directory import _load_people

    async with session_scope() as session:
        # Insert in deliberately-jumbled order; full_name ranges across cases.
        session.add_all([
            User(email="zoe-sort@test.com", hashed_password="x", is_active=True, full_name="Zoe"),
            User(email="alice-sort@test.com", hashed_password="x", is_active=True, full_name="alice"),  # lowercase
            User(email="bob-sort@test.com", hashed_password="x", is_active=True, full_name="Bob"),
            # Falls back to email when full_name is missing — email should sort by its lowercase form.
            User(email="charlie-sort@test.com", hashed_password="x", is_active=True, full_name=None),
        ])
        await session.commit()

    people = await _load_people()
    sortable_names = [
        (p.full_name or p.email).lower()
        for p in people
        if p.email.endswith("-sort@test.com")
    ]
    assert sortable_names == sorted(sortable_names)


async def test_disable_button_visible_for_superuser_on_ad_user(user: User):
    """Superuser viewing an AD user (not self) sees the Disable button."""
    from nicegui import ui
    from not_dot_net.backend.db import User as UserModel, AuthMethod
    from not_dot_net.frontend.directory import _render_detail

    import uuid
    superuser = UserModel(
        id=uuid.uuid4(),
        email="root@test", hashed_password="x", is_active=True, is_superuser=True,
        full_name="Root", role="admin",
    )
    ad_target = UserModel(
        id=uuid.uuid4(),
        email="alice@example.com", hashed_password="!ldap-no-local-password",
        is_active=True, full_name="Alice",
        auth_method=AuthMethod.LDAP, ldap_dn="cn=alice,dc=example,dc=com",
    )

    @ui.page("/_disable_button_super_ad")
    async def _page():
        container = ui.column()
        with container:
            await _render_detail(container, ad_target, superuser, state={})

    await user.open("/_disable_button_super_ad")
    await user.should_see("Disable account")


async def test_disable_button_hidden_for_non_superuser(user: User):
    """A regular admin (not superuser) should NOT see the Disable button."""
    from nicegui import ui
    from not_dot_net.backend.db import User as UserModel, AuthMethod
    from not_dot_net.frontend.directory import _render_detail

    import uuid
    admin = UserModel(
        id=uuid.uuid4(),
        email="admin@test", hashed_password="x", is_active=True,
        is_superuser=False, full_name="Admin", role="admin",
    )
    ad_target = UserModel(
        id=uuid.uuid4(),
        email="alice2@example.com", hashed_password="!ldap-no-local-password",
        is_active=True, full_name="Alice2",
        auth_method=AuthMethod.LDAP, ldap_dn="cn=alice2,dc=example,dc=com",
    )

    @ui.page("/_disable_button_admin")
    async def _page():
        container = ui.column()
        with container:
            await _render_detail(container, ad_target, admin, state={})

    await user.open("/_disable_button_admin")
    await user.should_not_see("Disable account")


async def test_disable_button_hidden_for_local_user(user: User):
    """The button must not appear for local (non-AD) users even for a superuser."""
    from nicegui import ui
    from not_dot_net.backend.db import User as UserModel, AuthMethod
    from not_dot_net.frontend.directory import _render_detail

    import uuid
    superuser = UserModel(
        id=uuid.uuid4(),
        email="root2@test", hashed_password="x", is_active=True, is_superuser=True,
        full_name="Root2", role="admin",
    )
    local_target = UserModel(
        id=uuid.uuid4(),
        email="bob@test", hashed_password="x", is_active=True, full_name="Bob",
        auth_method=AuthMethod.LOCAL,
    )

    @ui.page("/_disable_button_local")
    async def _page():
        container = ui.column()
        with container:
            await _render_detail(container, local_target, superuser, state={})

    await user.open("/_disable_button_local")
    await user.should_not_see("Disable account")


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


def test_classify_updates_maps_all_ad_synced_fields():
    from not_dot_net.backend.auth.ldap import AD_ATTR_MAP

    updates = {field: f"value-for-{field}" for field in AD_ATTR_MAP}
    ad_changes, local_updates = classify_updates(updates)

    assert local_updates == {}
    assert ad_changes == {
        ad_attr: f"value-for-{field}"
        for field, ad_attr in AD_ATTR_MAP.items()
    }


SELF_EDITABLE_AD_FIELDS = {"phone", "office"}
ADMIN_EDITABLE_AD_FIELDS = {"phone", "office", "full_name", "title", "team", "email"}


def test_self_editable_ad_fields_are_subset_of_admin_editable():
    assert SELF_EDITABLE_AD_FIELDS.issubset(ADMIN_EDITABLE_AD_FIELDS)


def test_self_editable_ad_fields_map_to_valid_ad_attributes():
    from not_dot_net.backend.auth.ldap import AD_ATTR_MAP
    for f in SELF_EDITABLE_AD_FIELDS:
        assert f in AD_ATTR_MAP


from not_dot_net.frontend.directory import compute_update_diff


def test_compute_update_diff_treats_empty_string_as_no_change_when_current_is_none():
    current = {"phone": None}
    submitted = {"phone": ""}
    assert compute_update_diff(current, submitted) == {}


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


def test_compute_update_diff_preserves_date_values():
    from datetime import date

    current = {"start_date": date(2026, 1, 1)}
    submitted = {"start_date": date(2026, 2, 1)}
    assert compute_update_diff(current, submitted) == {"start_date": date(2026, 2, 1)}


def test_is_ad_writable_allows_everything_without_ad_restrictions():
    from not_dot_net.frontend.directory import _is_ad_writable

    assert _is_ad_writable("phone", None) is True
    assert _is_ad_writable("employment_status", None) is True


def test_is_ad_writable_allows_local_fields_even_when_ad_restricted():
    from not_dot_net.frontend.directory import _is_ad_writable

    assert _is_ad_writable("employment_status", set()) is True
    assert _is_ad_writable("start_date", set()) is True


def test_is_ad_writable_blocks_ad_field_not_reported_writable():
    from not_dot_net.frontend.directory import _is_ad_writable

    assert _is_ad_writable("phone", {"physicalDeliveryOfficeName"}) is False


def test_is_ad_writable_allows_ad_field_reported_writable():
    from not_dot_net.frontend.directory import _is_ad_writable

    assert _is_ad_writable("phone", {"telephoneNumber"}) is True


async def test_self_edit_phone_writes_to_ad_and_local_db():
    from not_dot_net.backend.db import User, AuthMethod, session_scope
    from not_dot_net.backend.auth.ldap import (
        ldap_config, set_ldap_connect, ldap_modify_user,
    )
    from not_dot_net.frontend.directory import _update_user
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
        user_id = user.id

    cfg = await ldap_config.get()
    ldap_modify_user(
        dn=USER_DN,
        changes={"telephoneNumber": "+33NEW"},
        bind_username="jdoe", bind_password="secret",
        ldap_cfg=cfg, connect=fake_connect,
    )
    await _update_user(user_id, {"phone": "+33NEW"})

    assert ad_state["telephoneNumber"] == "+33NEW"

    async with session_scope() as session:
        refreshed = await session.get(User, user_id)
        assert refreshed.phone == "+33NEW"


async def test_delete_user_requires_manage_users():
    """R-10: deletion must be enforced server-side, not only at render time."""
    import pytest
    import uuid as _uuid

    from not_dot_net.backend.db import User
    from not_dot_net.frontend.directory import _delete_user

    async with session_scope() as session:
        target = User(id=_uuid.uuid4(), email="del-target@test.com", hashed_password="x", role="")
        actor = User(id=_uuid.uuid4(), email="del-actor@test.com", hashed_password="x", role="")
        session.add(target)
        session.add(actor)
        await session.commit()
        await session.refresh(target)
        await session.refresh(actor)

    with pytest.raises(PermissionError):
        await _delete_user(target.id, actor=actor)


def test_parse_optional_date_handles_blank_and_invalid_input():
    """R-13: free-text date fields crashed the save callback on typos."""
    import pytest
    from datetime import date as _date

    from not_dot_net.frontend.directory import parse_optional_date

    assert parse_optional_date("") is None
    assert parse_optional_date(None) is None
    assert parse_optional_date(" 2026-06-10 ") == _date(2026, 6, 10)
    with pytest.raises(ValueError):
        parse_optional_date("10/06/2026")


class TestWebpageUrlSafety:
    """S1 — a user-controlled `webpage` must never render as a javascript: link.

    The value reaches the DB from two directions: the directory edit form and
    AD sync (`wWWHomePage`), so the guard has to sit on the render side too.
    """

    def test_rejects_dangerous_schemes(self):
        from not_dot_net.frontend.directory import safe_external_url

        for value in (
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "  javascript:alert(1)",
            "java\tscript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
            "mailto:someone@example.com",
        ):
            assert safe_external_url(value) is None, f"{value!r} should be rejected"

    def test_accepts_http_and_https(self):
        from not_dot_net.frontend.directory import safe_external_url

        assert safe_external_url("https://lpp.polytechnique.fr") == "https://lpp.polytechnique.fr"
        assert safe_external_url("http://example.com/~user") == "http://example.com/~user"
        assert safe_external_url("  https://example.com  ") == "https://example.com"

    def test_bare_domain_gets_https_scheme(self):
        from not_dot_net.frontend.directory import safe_external_url

        assert safe_external_url("example.com/~me") == "https://example.com/~me"

    def test_empty_is_none(self):
        from not_dot_net.frontend.directory import safe_external_url

        assert safe_external_url("") is None
        assert safe_external_url(None) is None
        assert safe_external_url("   ") is None
