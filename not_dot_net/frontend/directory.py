import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import date
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response
from nicegui import ui
from sqlalchemy import func, select
from sqlalchemy.orm import defer


from not_dot_net.backend.audit import log_audit
from not_dot_net.backend.db import User, AuthMethod, session_scope, get_user_db
from not_dot_net.backend.profile_photo import (
    profile_photo_data_uri,
    profile_photo_mime,
    profile_photo_max_bytes,
    remove_profile_photo,
    save_profile_photo,
    validate_profile_photo_upload,
)
from not_dot_net.backend.schemas import UserUpdate
from not_dot_net.backend.users import current_active_user_optional, get_user_manager
from not_dot_net.frontend.i18n import format_datetime, get_locale, t
from not_dot_net.frontend.errors import notify_error
from not_dot_net.frontend.widgets import confirm_dialog, options_with, show_password_once
from not_dot_net.backend.permissions import (
    MANAGE_ROLES, check_permission, has_permissions, permission,
)
from not_dot_net.backend.ad_account_config import ad_account_config
from not_dot_net.backend.auth.ldap import AD_ATTR_MAP, LdapModifyError, ldap_config, ldap_reset_password
from not_dot_net.backend.workflow_ad_account import generate_initial_password
from not_dot_net.frontend.ad_credentials import prompt_ad_credentials
from not_dot_net.config import files_config

MANAGE_USERS = permission("manage_users", "Manage users", "Edit/delete users in directory")

SELF_EDITABLE_FIELDS = frozenset({"phone", "office", "company", "description", "webpage"})


def parse_optional_date(value: str | None) -> date | None:
    """Empty input → None; invalid format raises ValueError (ISO only)."""
    if not value or not value.strip():
        return None
    return date.fromisoformat(value.strip())


def _serialize_value(v) -> str | None:
    """Convert a value to a JSON-friendly string for audit logging."""
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def safe_external_url(value: str | None) -> str | None:
    """Return an http(s) URL safe to use as an href, or None if there isn't one.

    `webpage` is user-controlled and also round-trips from AD (`wWWHomePage`),
    so `javascript:`/`data:` payloads must never reach ui.link. Whitespace is
    stripped everywhere (not just at the ends) so `java\tscript:` can't sneak a
    scheme past the check. Scheme-less input is assumed https.
    """
    candidate = "".join((value or "").split())
    if not candidate:
        return None
    scheme = urlparse(candidate).scheme
    if scheme:
        return candidate if scheme in ("http", "https") else None
    return f"https://{candidate.lstrip('/')}"


def classify_updates(updates: dict) -> tuple[dict[str, str | None], dict]:
    """Split a user-update dict into (AD attribute changes, local-only DB updates).

    AD changes are keyed by AD attribute name (telephoneNumber, ...).
    """
    ad_changes: dict[str, str | None] = {}
    local_updates: dict = {}
    for field, value in updates.items():
        ad_attr = AD_ATTR_MAP.get(field)
        if ad_attr is not None:
            ad_changes[ad_attr] = value
        else:
            local_updates[field] = value
    return ad_changes, local_updates


def _blank_to_none(value):
    return None if value == "" else value


def compute_update_diff(current: dict, submitted: dict) -> dict:
    """Return only fields whose submitted value differs from current. Empty strings -> None."""
    return {
        k: _blank_to_none(raw)
        for k, raw in submitted.items()
        if _blank_to_none(raw) != _blank_to_none(current.get(k))
    }


directory_router = APIRouter(tags=["directory"])

# simplify: the URL is versioned by photo size, so a same-size replacement can
# show the old photo until max-age expires. Upgrade: a photo_updated_at column.
_PHOTO_MAX_AGE_S = 3600


@directory_router.get("/directory/photo/{user_id}")
async def serve_profile_photo(user_id: str, viewer=Depends(current_active_user_optional)):
    """Unauthenticated for active people, like the directory itself (LAN only).

    Inactive people are listed to user managers only, so their photos are too.
    """
    try:
        person = await _load_person(uuid.UUID(user_id))
    except ValueError:
        raise HTTPException(status_code=404)
    if person is None or not person.photo or profile_photo_mime(person.photo) is None:
        raise HTTPException(status_code=404)
    if not person.is_active and not await has_permissions(viewer, MANAGE_USERS):
        raise HTTPException(status_code=404)
    return Response(
        content=person.photo,
        media_type=profile_photo_mime(person.photo),
        headers={"Cache-Control": f"private, max-age={_PHOTO_MAX_AGE_S}"},
    )


def photo_url(user_id, photo_size: int) -> str:
    return f"/directory/photo/{user_id}?v={photo_size}"


async def _photo_sizes() -> dict[uuid.UUID, int]:
    async with session_scope() as session:
        rows = await session.execute(select(User.id, func.length(User.photo)).where(User.photo.is_not(None)))
        return dict(rows.all())


async def _load_person(user_id) -> User | None:
    async with session_scope() as session:
        return await session.get(User, user_id)


async def _load_people(include_inactive: bool = False) -> list[User]:
    """Without photo bytes: cards fetch them by URL."""
    async with session_scope() as session:
        query = (
            select(User)
            .options(defer(User.photo, raiseload=True))
            .order_by(func.lower(func.coalesce(User.full_name, User.email)))
        )
        if not include_inactive:
            query = query.where(User.is_active == True)  # noqa: E712
        result = await session.execute(query)
        return result.scalars().all()


async def authorize_user_update(actor, target: User, updates: dict) -> None:
    """Raise PermissionError unless `actor` may apply `updates` to `target`.

    A role grants a permission set, so changing one is role administration
    (`manage_roles`), not user administration — otherwise any `manage_users`
    holder could hand themselves admin.
    """
    if actor is None:
        raise PermissionError("No actor provided")
    is_own = getattr(actor, "id", None) == target.id
    if not (is_own and set(updates) <= SELF_EDITABLE_FIELDS):
        await check_permission(actor, MANAGE_USERS)
    if "role" in updates:
        await check_permission(actor, MANAGE_ROLES)
    if target.is_superuser and not actor.is_superuser:
        raise PermissionError("Only a superuser can edit a superuser")


async def _update_user(user_id, updates: dict, *, actor):
    """Update a user via UserManager (respects FastAPI-Users hooks)."""
    if "role" in updates:
        # User.role is NOT NULL; "no role" is the empty string.
        updates = {**updates, "role": updates["role"] or ""}
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                user = await manager.get(user_id)
                await authorize_user_update(actor, user, updates)
                update_schema = UserUpdate(**updates)
                # safe=True: FastAPI-Users strips is_superuser/is_active/is_verified
                # from self-service updates. The edit form never sets them; this
                # guards against a stray privileged key ever reaching the sink.
                await manager.update(
                    update_schema, user, safe=True, actor_email=getattr(actor, "email", None),
                )


async def _delete_user(user_id, actor=None):
    """Delete a user via UserManager (respects FastAPI-Users hooks).

    Audited before the delete: this cascades tenures, bookings and availability
    windows and SET NULLs the user out of workflow history, so afterwards there
    is nothing left to describe who was removed.
    """
    await check_permission(actor, MANAGE_USERS)
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                user = await manager.get(user_id)
                victim = f"{user.full_name or ''} <{user.email}>".strip()
                await manager.delete(user)
    await log_audit(
        "users", "delete",
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        target_type="user", target_id=user_id,
        detail=f"hard delete of {victim}",
    )


def render(current_user: User, focus_id: str | None = None):
    """`focus_id` opens on that person's card, alone (the "My profile" link)."""
    is_admin = False

    with ui.row().classes("items-center gap-4 w-full mb-4"):
        search = ui.input(placeholder=t("search_placeholder")).props(
            "outlined dense clearable"
        ).classes("flex-grow")
        show_inactive = ui.switch(t("show_inactive"), value=False).classes("ml-auto")

    card_container = ui.element("div").classes(
        "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 w-full"
    )
    no_match = ui.label(t("directory_no_match")).classes("text-grey")
    no_match.set_visibility(False)

    state = {"expanded_id": None, "details": {}, "toggles": {}}

    async def refresh():
        nonlocal is_admin
        is_admin = await has_permissions(current_user, "manage_users")
        show_inactive.set_visibility(is_admin)
        people = await _load_people(include_inactive=is_admin and show_inactive.value)
        photo_sizes = await _photo_sizes()
        state["expanded_id"] = None
        state["details"] = {}
        state["toggles"] = {}
        card_container.clear()
        with card_container:
            for person in people:
                _person_card(person, current_user, state, photo_sizes.get(person.id))
        filter_cards()
        await _open_focused(people)

    async def _open_focused(people):
        # Every refresh reopens it: the URL names this person, and the shell
        # refreshes the tab again right after the page loads.
        target = next((p for p in people if str(p.id) == focus_id), None)
        if target is None:
            return
        search.set_value(target.full_name or target.email)
        await state["toggles"][target.id]()

    def filter_cards():
        query = (search.value or "").strip().lower()
        shown = 0
        for child in card_container.default_slot.children:
            if hasattr(child, "_person_search_text"):
                visible = (not query) or (query in child._person_search_text)
                child.set_visibility(visible)
                shown += visible
        no_match.set_visibility(bool(query) and shown == 0)

    search.on_value_change(lambda _: filter_cards())
    show_inactive.on_value_change(lambda _: refresh())

    ui.timer(0, refresh, once=True)
    return refresh


def _format_duration(person: User) -> str:
    """Human-readable duration string for a person."""
    if not person.start_date:
        return ""
    today = date.today()
    if person.end_date:
        return f"{person.start_date} → {person.end_date}"
    delta = today - person.start_date
    years = delta.days // 365
    if years >= 1:
        return f"{t('since')} {person.start_date} ({years}y)"
    months = delta.days // 30
    if months >= 1:
        return f"{t('since')} {person.start_date} ({t('months_short', n=months)})"
    return f"{t('since')} {person.start_date}"


def _person_card(person: User, current_user: User, state: dict, photo_size: int | None = None):
    display_name = person.full_name or person.email
    search_text = " ".join(
        s.lower() for s in [
            person.full_name or "", person.email,
            person.team or "", person.office or "",
            person.title or "", person.employment_status or "",
            person.company or "",
        ]
    )

    with ui.card() as card:
        card._person_search_text = search_text

        header = ui.row().classes("items-center gap-3 cursor-pointer w-full")
        with header:
            if photo_size:
                ui.image(photo_url(person.id, photo_size)).classes(
                    "w-12 h-12 rounded-full object-cover"
                )
            else:
                ui.icon("person", size="xl").classes(
                    "rounded-full bg-gray-200 p-2"
                )
            with ui.column().classes("gap-0"):
                ui.label(display_name).classes("font-bold")
                if person.team:
                    subtitle = person.team
                    if person.company:
                        subtitle += f" — {person.company}"
                    ui.label(subtitle).classes("text-sm text-gray-500")
                elif person.company:
                    ui.label(person.company).classes("text-sm text-gray-500")
                if person.office:
                    ui.label(f"{t('office')} {person.office}").classes("text-sm text-gray-500")
                duration = _format_duration(person)
                if duration:
                    ui.label(duration).classes("text-xs text-gray-400")

        detail_container = ui.column().classes("w-full mt-2")
        detail_container.set_visibility(False)
        state["details"][person.id] = detail_container

        async def toggle_expand():
            currently_expanded = state["expanded_id"]
            if currently_expanded == person.id:
                detail_container.set_visibility(False)
                state["expanded_id"] = None
            else:
                if currently_expanded and currently_expanded in state["details"]:
                    state["details"][currently_expanded].set_visibility(False)
                detail_container.set_visibility(True)
                state["expanded_id"] = person.id
                await _render_detail(detail_container, person, current_user, state)

        header.on("click", toggle_expand)
        state["toggles"][person.id] = toggle_expand


async def _render_detail(container, person: User, current_user: User, state: dict):
    container.clear()
    is_own = person.id == current_user.id
    is_admin = await has_permissions(current_user, "manage_users")

    with container:
        ui.separator()
        if person.phone:
            ui.label(f"{t('phone')}: {person.phone}").classes("text-sm")
        ui.label(f"{t('email')}: {person.email}").classes("text-sm")
        if person.company:
            ui.label(f"{t('company')}: {person.company}").classes("text-sm")
        if person.employment_status:
            status_labels, _ = await _tenure_options(get_locale())
            status = status_labels.get(person.employment_status, person.employment_status)
            ui.label(f"{t('status')}: {status}").classes("text-sm")
        if person.title:
            ui.label(f"{t('title')}: {person.title}").classes("text-sm")
        if person.description:
            ui.label(f"{t('description')}: {person.description}").classes("text-sm")
        if person.webpage:
            with ui.row().classes("items-center gap-1"):
                ui.label(f"{t('webpage')}:").classes("text-sm")
                href = safe_external_url(person.webpage)
                if href:
                    ui.link(person.webpage, href, new_tab=True).classes("text-sm")
                else:
                    ui.label(person.webpage).classes("text-sm")
        if person.start_date:
            ui.label(f"{t('start_date')}: {person.start_date}").classes("text-sm")
        if person.end_date:
            ui.label(f"{t('end_date')}: {person.end_date}").classes("text-sm")
        if is_admin:
            if person.uid_number is not None or person.gid_number is not None:
                parts = []
                if person.uid_number is not None:
                    parts.append(f"{t('uid_number')}: {person.uid_number}")
                if person.gid_number is not None:
                    parts.append(f"{t('gid_number')}: {person.gid_number}")
                ui.label(" | ".join(parts)).classes("text-sm text-gray-500")
            if person.member_of:
                cn_names = [dn.split(",")[0].removeprefix("CN=") for dn in person.member_of]
                ui.label(f"{t('member_of')}: {', '.join(cn_names)}").classes("text-sm text-gray-500")

        if current_user.is_superuser and person.last_ad_logon is not None:
            stamp = format_datetime(person.last_ad_logon)
            ui.label(f"{t('last_ad_logon')}: {stamp}").classes("text-sm text-gray-500")

        if is_own or is_admin:
            async def do_edit():
                await _render_edit(container, person, current_user, state)

            ui.button(t("edit"), icon="edit", on_click=do_edit).props("flat dense")

        if is_admin and not is_own:
            display = person.full_name or person.email
            with ui.dialog() as confirm_dialog, ui.card():
                ui.label(t("confirm_delete", name=display))
                with ui.row():
                    ui.button(t("cancel"), on_click=confirm_dialog.close).props("flat")

                    async def do_delete():
                        confirm_dialog.close()
                        try:
                            await _delete_user(person.id, actor=current_user)
                        except PermissionError:
                            ui.notify(t("access_denied"), color="negative")
                            return
                        ui.notify(t("deleted", name=display), color="positive")
                        container.parent_slot.parent.set_visibility(False)

                    ui.button(t("delete"), on_click=do_delete).props(
                        "flat color=negative"
                    )

            ui.button(t("delete"), icon="delete", on_click=confirm_dialog.open).props(
                "flat dense color=negative"
            )

        is_ad_user = person.auth_method == AuthMethod.LDAP and person.ldap_dn
        if current_user.is_superuser and is_ad_user and not is_own:
            _render_ad_enable_disable_button(container, person, current_user)
            _render_reset_password_button(person, current_user)

        if is_own or is_admin:
            ui.separator().classes("my-2")
            await _render_tenure_history(container, person, current_user, is_admin)


async def _render_edit(container, person: User, current_user: User, state: dict):
    # The card's copy is stale and has no photo bytes.
    person = await _load_person(person.id)
    if person is None:
        return
    is_ldap = person.auth_method == AuthMethod.LDAP and person.ldap_dn
    if not is_ldap:
        await _render_edit_form(container, person, current_user, state,
                                ad_writable=None, stored_conn=None)
        return

    from not_dot_net.backend.auth.ldap import get_user_connection, _query_writable_attributes

    conn = get_user_connection(str(current_user.id))
    writable: set[str] | None = set()
    probe_failed = conn is None
    if conn is not None:
        try:
            writable = await asyncio.to_thread(_query_writable_attributes, conn, person.ldap_dn)
        except Exception:
            probe_failed = True
            writable = set()

    await _render_edit_form(container, person, current_user, state,
                            ad_writable=writable, stored_conn=conn,
                            ad_probe_failed=probe_failed)


def _prompt_ad_credentials_then_save(person, current_user, save_callback):
    """Adapter — kept so existing call site doesn't change.

    Wraps the shared `prompt_ad_credentials` dialog; fetches the cached
    connection after bind to pass as `ad_conn=` to save_callback.
    """
    import asyncio

    from not_dot_net.backend.auth.ldap import get_user_connection
    from not_dot_net.frontend.ad_credentials import prompt_ad_credentials

    async def _on_bind(_bind_user, _bind_pw):
        await save_callback(ad_conn=get_user_connection(str(current_user.id)))

    asyncio.create_task(prompt_ad_credentials(current_user, _on_bind))


def _render_ad_enable_disable_button(container, person: User, current_user: User) -> None:
    """Render an AD enable/disable button on the person detail card.

    Caller must already have verified `current_user.is_superuser`, the target
    is an AD user, and not viewing self. Pushes the change to AD via the
    bound admin's credentials, then updates local `is_active` for immediate
    UI feedback.
    """
    from not_dot_net.backend.auth.ldap import (
        LdapModifyError, ldap_config, ldap_set_account_enabled,
    )
    from not_dot_net.backend.db import session_scope as _session_scope

    display = person.full_name or person.email
    enabling = not person.is_active
    label = t("enable_account") if enabling else t("disable_account")
    color = "positive" if enabling else "negative"
    icon = "check_circle" if enabling else "block"
    confirm_msg = (t("confirm_enable_account", name=display) if enabling
                   else t("confirm_disable_account", name=display))

    async def push_to_ad(bind_user: str, bind_password: str) -> None:
        cfg = await ldap_config.get()
        try:
            await asyncio.to_thread(
                ldap_set_account_enabled,
                dn=person.ldap_dn, enabled=enabling,
                bind_username=bind_user, bind_password=bind_password,
                ldap_cfg=cfg,
            )
        except LdapModifyError as e:
            notify_error(e)
            return
        async with _session_scope() as session:
            db_person = await session.get(User, person.id)
            if db_person is not None:
                db_person.is_active = enabling
                await session.commit()
        await log_audit(
            "users", "enable" if enabling else "disable",
            actor_id=current_user.id, actor_email=current_user.email,
            target_type="user", target_id=person.id,
            detail=f"ad_dn={person.ldap_dn}",
        )
        ui.notify(
            t("account_enabled" if enabling else "account_disabled", name=display),
            color="positive",
        )
        container.parent_slot.parent.set_visibility(False)

    def open_creds_prompt() -> None:
        creds_dialog = ui.dialog()
        with creds_dialog, ui.card():
            ui.label(t("admin_ad_credentials"))
            username_input = ui.input(t("ad_admin_username")).props("outlined dense")
            password_input = ui.input(t("password"), password=True).props("outlined dense")

            async def submit():
                bind_user = username_input.value.strip()
                if not bind_user or not password_input.value:
                    return
                creds_dialog.close()
                await push_to_ad(bind_user, password_input.value)

            with ui.row():
                ui.button(t("submit"), on_click=submit).props("flat color=primary")
                ui.button(t("cancel"), on_click=creds_dialog.close).props("flat")
        creds_dialog.open()

    confirm_dialog = ui.dialog()
    with confirm_dialog, ui.card():
        ui.label(confirm_msg)
        with ui.row():
            ui.button(t("cancel"), on_click=confirm_dialog.close).props("flat")

            def on_confirm():
                confirm_dialog.close()
                open_creds_prompt()

            ui.button(label, on_click=on_confirm).props(f"flat color={color}")

    ui.button(label, icon=icon, on_click=confirm_dialog.open).props(
        f"flat dense color={color}"
    )


def _render_reset_password_button(person: User, current_user: User) -> None:
    """IT resets someone's AD password; the new one is shown once, never stored.

    Same gate as enable/disable (superuser, AD user, not self). Binds with the
    acting admin's credentials: there is no service account to do it with.
    """
    display = person.full_name or person.email

    async def reset(bind_user: str, bind_password: str) -> None:
        password = generate_initial_password((await ad_account_config.get()).password_length)
        try:
            await asyncio.to_thread(
                ldap_reset_password, person.ldap_dn, password, bind_user, bind_password,
                await ldap_config.get(), must_change_password=must_change.value, unlock=True,
            )
        except LdapModifyError as e:
            notify_error(e)
            return
        await log_audit(
            "user", "reset_password",
            actor_id=current_user.id, actor_email=current_user.email,
            target_type="user", target_id=person.id,
            detail=f"ad_dn={person.ldap_dn} must_change={must_change.value}",
        )
        show_password_once(password, message_key="reset_password_copy_now")

    async def on_confirm():
        dialog.close()
        await prompt_ad_credentials(current_user, reset)

    with ui.dialog() as dialog, ui.card():
        ui.label(t("confirm_reset_password", name=display))
        must_change = ui.checkbox(t("reset_must_change"), value=True)
        with ui.row():
            ui.button(t("cancel"), on_click=dialog.close).props("flat")
            ui.button(t("reset_password"), on_click=on_confirm).props("flat color=warning").mark("reset-confirm")

    ui.button(t("reset_password"), icon="key", on_click=dialog.open).props("flat dense color=warning").mark("reset-open")


def _is_ad_writable(field_name: str, ad_writable: set[str] | None) -> bool:
    """Check if a local field is writable in AD. None means no AD restriction."""
    if ad_writable is None:
        return True
    ad_attr = AD_ATTR_MAP.get(field_name)
    if ad_attr is None:
        return True
    return ad_attr in ad_writable


async def _render_edit_form(container, person: User, current_user: User, state: dict,
                            *, ad_writable: set[str] | None, stored_conn=None,
                            ad_probe_failed: bool = False):
    container.clear()
    is_admin = await has_permissions(current_user, "manage_users")
    is_own = person.id == current_user.id

    with container:
        if ad_probe_failed:
            # Otherwise every AD-backed field just greys out with no reason
            # given, and the user assumes they lack permission.
            with ui.row().classes("items-center gap-1 mb-2"):
                ui.icon("info", color="warning").classes("text-sm")
                ui.label(t("ad_fields_readonly")).classes("text-xs text-grey-8")
        ui.separator()

        fields = {}
        readonly_fields: set[str] = set()

        def _add_field(name, label, value, *, readonly=False):
            widget = ui.input(label, value=value).props("outlined dense")
            if readonly or not _is_ad_writable(name, ad_writable):
                widget.props("readonly")
                widget.classes("opacity-60")
                readonly_fields.add(name)
            fields[name] = widget

        if is_admin:
            _add_field("full_name", t("full_name"), person.full_name or "")
            _add_field("email", t("email"), person.email)
            _add_field("team", t("team"), person.team or "")
            _add_field("title", t("title"), person.title or "")
            _add_field("employment_status", t("status"), person.employment_status or "")
            _add_field("start_date", t("start_date"),
                       str(person.start_date) if person.start_date else "")
            _add_field("end_date", t("end_date"),
                       str(person.end_date) if person.end_date else "")

        if await has_permissions(current_user, MANAGE_ROLES):
            from not_dot_net.backend.roles import roles_config
            roles_cfg = await roles_config.get()
            role_options = sorted(roles_cfg.roles.keys())
            current_role = person.role or ""
            if current_role and current_role not in role_options:
                role_options = sorted(role_options + [current_role])
            role_select = ui.select(
                role_options, value=current_role or None, label=t("role"),
            ).props("outlined dense stack-label clearable").classes("w-full")
            fields["role"] = role_select

        _add_field("phone", t("phone"), person.phone or "")
        _add_field("office", t("office"), person.office or "")
        _add_field("company", t("company"), person.company or "")
        _add_field("description", t("description"), person.description or "")
        _add_field("webpage", t("webpage"), person.webpage or "")

        files_cfg = await files_config.get()
        photo_container = ui.column().classes("w-full gap-2")

        async def render_photo_controls():
            photo_container.clear()
            with photo_container:
                ui.label(t("photo")).classes("text-sm font-medium")
                photo_uri = profile_photo_data_uri(person.photo)
                if photo_uri:
                    ui.image(photo_uri).classes("w-24 h-24 rounded-full object-cover")
                else:
                    ui.icon("person", size="xl").classes("rounded-full bg-gray-200 p-3")

            def notify_photo_too_large():
                ui.notify(
                    t("profile_photo_too_large", max_size_mb=files_cfg.profile_photo_max_size_mb),
                    color="negative",
                )

            async def handle_photo_upload(event):
                upload = event.file
                # Size before read: max_file_size is only enforced in the browser.
                if upload.size() > profile_photo_max_bytes(files_cfg.profile_photo_max_size_mb):
                    notify_photo_too_large()
                    return
                content = await upload.read()
                filename = upload.name or ""
                error = await validate_profile_photo_upload(content, filename)
                if error:
                    if error == "profile_photo_too_large":
                        notify_photo_too_large()
                    else:
                        ui.notify(t(error), color="negative")
                    return
                try:
                    saved_photo = await save_profile_photo(person.id, content, actor=current_user)
                except PermissionError:
                    ui.notify(t("access_denied"), color="negative")
                    return
                if saved_photo is None:
                    ui.notify(t("profile_photo_upload_failed"), color="negative")
                    return
                await log_audit(
                    "user", "update",
                    actor_id=current_user.id, actor_email=current_user.email,
                    target_type="user", target_id=person.id,
                    detail="fields=photo",
                    metadata={"changes": {"photo": {"old": bool(person.photo), "new": True}}},
                )
                ui.notify(t("profile_photo_updated"), color="positive")
                person.photo = saved_photo
                await render_photo_controls()

            ui.upload(
                label=t("upload_profile_photo"),
                auto_upload=True,
                max_file_size=profile_photo_max_bytes(files_cfg.profile_photo_max_size_mb),
                on_upload=handle_photo_upload,
                on_rejected=notify_photo_too_large,
            ).props("outlined flat accept='.jpg,.jpeg,.png'").classes("w-full max-w-sm")

            if person.photo:
                async def do_remove_photo():
                    try:
                        removed = await remove_profile_photo(person.id, actor=current_user)
                    except PermissionError:
                        ui.notify(t("access_denied"), color="negative")
                        return
                    if not removed:
                        ui.notify(t("profile_photo_upload_failed"), color="negative")
                        return
                    await log_audit(
                        "user", "update",
                        actor_id=current_user.id, actor_email=current_user.email,
                        target_type="user", target_id=person.id,
                        detail="fields=photo",
                        metadata={"changes": {"photo": {"old": True, "new": False}}},
                    )
                    ui.notify(t("profile_photo_removed"), color="positive")
                    person.photo = None
                    await render_photo_controls()

                ui.button(
                    t("remove_profile_photo"),
                    icon="delete",
                    on_click=do_remove_photo,
                ).props("flat dense color=negative")

        await render_photo_controls()

        async def _do_save(ad_conn=None):
            submitted = {}
            for k, v in fields.items():
                if k in readonly_fields:
                    continue
                val = v.value or None
                if k in ("start_date", "end_date"):
                    try:
                        val = parse_optional_date(val)
                    except ValueError:
                        ui.notify(t("invalid_date"), color="negative")
                        return
                if k == "webpage" and val:
                    val = safe_external_url(val)
                    if val is None:
                        ui.notify(t("invalid_webpage"), color="negative")
                        return
                submitted[k] = val

            current = {k: getattr(person, k) for k in submitted}
            diff = compute_update_diff(current, submitted)
            if not diff:
                ui.notify(t("saved"), color="positive")
                return

            try:
                await authorize_user_update(current_user, person, diff)
            except PermissionError:
                ui.notify(t("access_denied"), color="negative")
                return

            ad_changes, local_updates = classify_updates(diff)
            needs_ad_write = bool(ad_changes) and person.auth_method == AuthMethod.LDAP

            if needs_ad_write:
                from ldap3 import MODIFY_REPLACE
                conn = ad_conn
                if conn is None:
                    from not_dot_net.backend.auth.ldap import get_user_connection
                    conn = get_user_connection(str(current_user.id))
                if conn is None or not conn.bound:
                    _prompt_ad_credentials_then_save(person, current_user, _do_save)
                    return
                modify_payload = {
                    attr: [(MODIFY_REPLACE, [val] if val else [])]
                    for attr, val in ad_changes.items()
                    if ad_writable is None or attr in ad_writable
                }
                if modify_payload:
                    ok = await asyncio.to_thread(conn.modify, person.ldap_dn, modify_payload)
                    if not ok:
                        ui.notify(
                            t("ad_write_failed", error=conn.result.get("description", "")),
                            color="negative",
                        )
                        return

            await _update_user(person.id, diff, actor=current_user)
            current_values = {k: getattr(person, k) for k in diff}
            changes = {
                k: {"old": _serialize_value(current_values.get(k)), "new": _serialize_value(v)}
                for k, v in diff.items()
            }
            await log_audit(
                "user", "update",
                actor_id=current_user.id, actor_email=current_user.email,
                target_type="user", target_id=person.id,
                detail=f"fields={','.join(diff.keys())}",
                metadata={"changes": changes},
            )
            await _finish_save(container, person, current_user, state)

        async def save():
            await _do_save(stored_conn)

        with ui.row():
            ui.button(t("save"), on_click=save).props("flat dense color=primary")

            async def do_cancel():
                await _render_detail(container, person, current_user, state)

            ui.button(t("cancel"), on_click=do_cancel).props("flat dense")


async def _finish_save(container, person, current_user, state):
    ui.notify(t("saved"), color="positive")
    updated = await _load_person(person.id) or person
    await _render_detail(container, updated, current_user, state)


async def _render_tenure_history(parent_container, person: User, current_user: User, is_admin: bool):
    """Render the employment history timeline for a person."""
    from not_dot_net.backend.tenure_service import list_tenures as _list_tenures

    tenures = await _list_tenures(person.id)
    status_labels, _ = await _tenure_options(get_locale())

    with ui.expansion(t("tenure_history"), icon="history").classes("w-full"):
        tenure_container = ui.column().classes("w-full")

        async def refresh_tenures():
            nonlocal tenures
            tenures = await _list_tenures(person.id)
            tenure_container.clear()
            with tenure_container:
                if not tenures:
                    ui.label(t("no_tenures")).classes("text-sm text-gray-400 italic")
                for ten in tenures:
                    _render_tenure_row(ten, is_admin, refresh_tenures, person, current_user, status_labels)

        if is_admin:
            async def show_add():
                await _tenure_add_dialog(person, current_user, refresh_tenures)
            ui.button(t("add_tenure"), icon="add", on_click=show_add).props("flat dense")

        await refresh_tenures()


def _render_tenure_row(tenure, is_admin: bool, on_refresh, person: User, current_user: User,
                       status_labels: dict[str, str]):
    end_label = t("tenure_current") if tenure.end_date is None else str(tenure.end_date)
    with ui.row().classes("items-center gap-2 w-full"):
        ui.chip(status_labels.get(tenure.status, tenure.status), color="primary").props("dense outline")
        ui.label(f"{tenure.employer}").classes("text-sm font-medium")
        if tenure.team:
            ui.label(tenure.team).classes("text-sm")
        ui.label(f"{tenure.start_date} → {end_label}").classes("text-sm text-gray-500")
        if tenure.notes:
            ui.icon("info", size="xs").tooltip(tenure.notes)
        if is_admin:
            async def do_edit(t_id=tenure.id):
                await _tenure_edit_dialog(t_id, person, current_user, on_refresh)
            ui.button(icon="edit", on_click=do_edit).props(
                "flat dense round size=xs"
            ).tooltip(t("edit")).mark("edit-tenure")

            async def do_delete(t_id=tenure.id):
                from not_dot_net.backend.tenure_service import delete_tenure as _del
                await _del(t_id, actor=current_user)
                await log_audit(
                    "user", "delete_tenure",
                    actor_id=current_user.id, actor_email=current_user.email,
                    target_type="user", target_id=person.id,
                )
                ui.notify(t("tenure_deleted"), color="positive")
                await on_refresh()
            delete_dlg = confirm_dialog(t("confirm_delete_tenure"), do_delete)
            ui.button(icon="delete", on_click=delete_dlg.open).props(
                "flat dense round size=xs color=negative"
            ).tooltip(t("delete")).mark("delete-tenure")


async def _tenure_options(locale: str) -> tuple[dict[str, str], dict[str, str]]:
    """(status_options, employer_options) for the tenure dialogs, from the registry."""
    from not_dot_net.backend.vocabularies import field_options
    status = (await field_options("employment_statuses", locale)).options
    employer = (await field_options("employers", locale)).options
    return status, employer


async def _team_select(current: str | None = None) -> ui.select:
    from not_dot_net.backend.vocabularies import field_options
    teams = (await field_options("teams", get_locale())).options
    return ui.select(
        options_with(teams, current) if current else teams, value=current, label=t("team"), clearable=True,
    ).props("outlined dense stack-label").mark("tenure-team")


async def _tenure_add_dialog(person: User, current_user: User, on_refresh):
    from not_dot_net.backend.tenure_service import add_tenure as _add
    from not_dot_net.frontend.i18n import get_locale

    status_opts, employer_opts = await _tenure_options(get_locale())

    dialog = ui.dialog()
    with dialog, ui.card().classes("w-96"):
        ui.label(t("add_tenure")).classes("text-h6")
        status_input = ui.select(status_opts, label=t("status")).props("outlined dense stack-label")
        employer_input = ui.select(employer_opts, label=t("employer")).props("outlined dense stack-label")
        team_input = await _team_select()
        start_input = ui.input(t("start_date"), placeholder="YYYY-MM-DD").props("outlined dense stack-label")
        end_input = ui.input(t("end_date"), placeholder=t("date_placeholder_optional")).props("outlined dense stack-label")
        notes_input = ui.input(t("tenure_notes")).props("outlined dense stack-label")

        async def save():
            if not status_input.value or not employer_input.value or not start_input.value:
                ui.notify(t("required_field"), color="warning")
                return
            try:
                start = parse_optional_date(start_input.value)
                end = parse_optional_date(end_input.value)
            except ValueError:
                ui.notify(t("invalid_date"), color="negative")
                return
            await _add(
                user_id=person.id,
                status=status_input.value,
                employer=employer_input.value,
                start_date=start,
                end_date=end,
                notes=notes_input.value or None,
                team=team_input.value or None,
                actor=current_user,
            )
            await log_audit(
                "user", "add_tenure",
                actor_id=current_user.id, actor_email=current_user.email,
                target_type="user", target_id=person.id,
                detail=f"status={status_input.value} employer={employer_input.value} team={team_input.value}",
            )
            dialog.close()
            ui.notify(t("tenure_saved"), color="positive")
            await on_refresh()

        with ui.row():
            ui.button(t("save"), on_click=save).props("flat color=primary")
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

    dialog.open()


async def _tenure_edit_dialog(tenure_id, person: User, current_user: User, on_refresh):
    from not_dot_net.backend.tenure_service import update_tenure as _update, list_tenures as _list
    from not_dot_net.frontend.i18n import get_locale

    status_opts, employer_opts = await _tenure_options(get_locale())
    tenures = await _list(person.id)
    tenure = next((ten for ten in tenures if ten.id == tenure_id), None)
    if tenure is None:
        return

    dialog = ui.dialog()
    with dialog, ui.card().classes("w-96"):
        ui.label(t("edit_tenure")).classes("text-h6")
        status_input = ui.select(
            options_with(status_opts, tenure.status), value=tenure.status, label=t("status"),
        ).props("outlined dense stack-label")
        employer_input = ui.select(
            options_with(employer_opts, tenure.employer), value=tenure.employer, label=t("employer"),
        ).props("outlined dense stack-label")
        team_input = await _team_select(tenure.team)
        start_input = ui.input(t("start_date"), value=str(tenure.start_date)).props("outlined dense stack-label")
        end_input = ui.input(t("end_date"), value=str(tenure.end_date) if tenure.end_date else "").props("outlined dense stack-label")
        notes_input = ui.input(t("tenure_notes"), value=tenure.notes or "").props("outlined dense stack-label")

        async def save():
            if not status_input.value or not employer_input.value or not start_input.value:
                ui.notify(t("required_field"), color="warning")
                return
            try:
                start = parse_optional_date(start_input.value)
                end = parse_optional_date(end_input.value)
            except ValueError:
                ui.notify(t("invalid_date"), color="negative")
                return
            await _update(
                tenure_id,
                status=status_input.value,
                employer=employer_input.value,
                start_date=start,
                end_date=end,
                notes=notes_input.value or None,
                team=team_input.value or None,
                actor=current_user,
            )
            await log_audit(
                "user", "update_tenure",
                actor_id=current_user.id, actor_email=current_user.email,
                target_type="user", target_id=person.id,
                detail=f"status={status_input.value} employer={employer_input.value} team={team_input.value}",
            )
            dialog.close()
            ui.notify(t("tenure_saved"), color="positive")
            await on_refresh()

        with ui.row():
            ui.button(t("save"), on_click=save).props("flat color=primary")
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

    dialog.open()
