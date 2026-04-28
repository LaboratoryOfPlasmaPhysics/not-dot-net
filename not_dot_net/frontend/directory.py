from contextlib import asynccontextmanager
from datetime import date

from nicegui import ui
from sqlalchemy import select


from not_dot_net.backend.db import User, AuthMethod, session_scope, get_user_db
from not_dot_net.backend.schemas import UserUpdate
from not_dot_net.backend.users import get_user_manager
from not_dot_net.frontend.i18n import t
from not_dot_net.backend.permissions import permission, has_permissions
from not_dot_net.backend.auth.ldap import AD_ATTR_MAP

MANAGE_USERS = permission("manage_users", "Manage users", "Edit/delete users in directory")


def _serialize_value(v) -> str | None:
    """Convert a value to a JSON-friendly string for audit logging."""
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


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


def compute_update_diff(current: dict, submitted: dict) -> dict:
    """Return only fields whose submitted value differs from current. Empty strings -> None."""
    out: dict = {}
    for k, raw in submitted.items():
        new_val = raw if raw not in ("", None) else None
        if new_val != current.get(k):
            out[k] = new_val
    return out


async def _load_people(include_inactive: bool = False) -> list[User]:
    async with session_scope() as session:
        query = select(User)
        if not include_inactive:
            query = query.where(User.is_active == True)  # noqa: E712
        result = await session.execute(query)
        return result.scalars().all()


async def _update_user(user_id, updates: dict):
    """Update a user via UserManager (respects FastAPI-Users hooks)."""
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                user = await manager.get(user_id)
                update_schema = UserUpdate(**updates)
                await manager.update(update_schema, user)


async def _delete_user(user_id):
    """Delete a user via UserManager (respects FastAPI-Users hooks)."""
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                user = await manager.get(user_id)
                await manager.delete(user)


def render(current_user: User):
    is_admin = False

    with ui.row().classes("items-center gap-4 w-full mb-4"):
        search = ui.input(placeholder=t("search_placeholder")).props(
            "outlined dense clearable"
        ).classes("flex-grow")
        show_inactive = ui.switch(t("show_inactive"), value=False).classes("ml-auto")

    card_container = ui.element("div").classes(
        "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 w-full"
    )

    state = {"expanded_id": None, "details": {}}

    async def refresh():
        nonlocal is_admin
        is_admin = await has_permissions(current_user, "manage_users")
        show_inactive.set_visibility(is_admin)
        people = await _load_people(include_inactive=is_admin and show_inactive.value)
        state["expanded_id"] = None
        state["details"] = {}
        card_container.clear()
        with card_container:
            for person in people:
                _person_card(person, current_user, state)

    def filter_cards():
        query = (search.value or "").strip().lower()
        for child in card_container.default_slot.children:
            if hasattr(child, "_person_search_text"):
                visible = (not query) or (query in child._person_search_text)
                child.set_visibility(visible)

    search.on_value_change(lambda _: filter_cards())
    show_inactive.on_value_change(lambda _: refresh())

    ui.timer(0, refresh, once=True)


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
        return f"{t('since')} {person.start_date} ({months}mo)"
    return f"{t('since')} {person.start_date}"


def _person_card(person: User, current_user: User, state: dict):
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
            if person.photo:
                import base64
                b64 = base64.b64encode(person.photo).decode()
                ui.image(f"data:image/jpeg;base64,{b64}").classes(
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
            ui.label(f"{t('status')}: {person.employment_status}").classes("text-sm")
        if person.title:
            ui.label(f"{t('title')}: {person.title}").classes("text-sm")
        if person.description:
            ui.label(f"{t('description')}: {person.description}").classes("text-sm")
        if person.webpage:
            with ui.row().classes("items-center gap-1"):
                ui.label(f"{t('webpage')}:").classes("text-sm")
                ui.link(person.webpage, person.webpage, new_tab=True).classes("text-sm")
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
                        await _delete_user(person.id)
                        ui.notify(t("deleted", name=display), color="positive")
                        container.parent_slot.parent.set_visibility(False)

                    ui.button(t("delete"), on_click=do_delete).props(
                        "flat color=negative"
                    )

            ui.button(t("delete"), icon="delete", on_click=confirm_dialog.open).props(
                "flat dense color=negative"
            )

        if is_own or is_admin:
            ui.separator().classes("my-2")
            await _render_tenure_history(container, person, current_user, is_admin)


async def _render_edit(container, person: User, current_user: User, state: dict):
    is_ldap = person.auth_method == AuthMethod.LDAP and person.ldap_dn
    if not is_ldap:
        await _render_edit_form(container, person, current_user, state,
                                ad_writable=None, stored_conn=None)
        return

    from not_dot_net.backend.auth.ldap import get_user_connection, _query_writable_attributes

    conn = get_user_connection(str(current_user.id))
    writable: set[str] | None = set()
    if conn is not None:
        try:
            writable = _query_writable_attributes(conn, person.ldap_dn)
        except Exception:
            writable = set()

    await _render_edit_form(container, person, current_user, state,
                            ad_writable=writable, stored_conn=conn)


def _prompt_ad_credentials_then_save(person, current_user, save_callback):
    """Prompt for AD credentials only when saving changes to AD-mapped fields."""
    from not_dot_net.backend.auth.ldap import (
        ldap_config, get_ldap_connect,
        _ldap_bind, LdapModifyError, store_user_connection,
    )

    dialog = ui.dialog()
    with dialog, ui.card():
        ui.label(t("confirm_password_to_save_ad"))
        username_input = ui.input(t("ad_admin_username")).props("outlined dense")
        password_input = ui.input(t("password"), password=True).props("outlined dense")
        error_label = ui.label("").classes("text-negative")

        async def submit():
            bind_user = username_input.value.strip()
            if not bind_user or not password_input.value:
                return
            cfg = await ldap_config.get()
            try:
                conn = _ldap_bind(bind_user, password_input.value, cfg, get_ldap_connect())
            except LdapModifyError as e:
                msg = str(e)
                error_label.set_text(
                    t("ad_bind_failed") if "bind" in msg.lower() else t("ad_write_failed", error=msg)
                )
                return
            store_user_connection(str(current_user.id), conn)
            dialog.close()
            await save_callback(ad_conn=conn)

        with ui.row():
            ui.button(t("submit"), on_click=submit).props("flat color=primary")
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

    dialog.open()


def _is_ad_writable(field_name: str, ad_writable: set[str] | None) -> bool:
    """Check if a local field is writable in AD. None means no AD restriction."""
    if ad_writable is None:
        return True
    ad_attr = AD_ATTR_MAP.get(field_name)
    if ad_attr is None:
        return True
    return ad_attr in ad_writable


async def _render_edit_form(container, person: User, current_user: User, state: dict,
                            *, ad_writable: set[str] | None, stored_conn=None):
    container.clear()
    is_admin = await has_permissions(current_user, "manage_users")
    is_own = person.id == current_user.id

    with container:
        ui.separator()

        fields = {}

        def _add_field(name, label, value, *, readonly=False):
            widget = ui.input(label, value=value).props("outlined dense")
            if readonly or not _is_ad_writable(name, ad_writable):
                widget.props("readonly")
                widget.classes("opacity-60")
            fields[name] = widget

        if is_admin:
            _add_field("full_name", t("full_name"), person.full_name or "")
            _add_field("email", t("email"), person.email)
            _add_field("team", t("team"), person.team or "")
            _add_field("employment_status", t("status"), person.employment_status or "")
            _add_field("title", t("title"), person.title or "")
            _add_field("start_date", t("start_date"),
                       str(person.start_date) if person.start_date else "")
            _add_field("end_date", t("end_date"),
                       str(person.end_date) if person.end_date else "")

        if is_admin and not is_own:
            from not_dot_net.backend.roles import roles_config
            roles_cfg = await roles_config.get()
            role_options = sorted(roles_cfg.roles.keys())
            role_select = ui.select(
                role_options, value=person.role or "", label=t("role"),
            ).props("outlined dense stack-label").classes("w-full")
            fields["role"] = role_select

        _add_field("phone", t("phone"), person.phone or "")
        _add_field("office", t("office"), person.office or "")
        _add_field("company", t("company"), person.company or "")
        _add_field("description", t("description"), person.description or "")
        _add_field("webpage", t("webpage"), person.webpage or "")

        async def _do_save(ad_conn=None):
            submitted = {}
            for k, v in fields.items():
                if not _is_ad_writable(k, ad_writable):
                    continue
                val = v.value or None
                if k in ("start_date", "end_date") and val:
                    val = date.fromisoformat(val)
                submitted[k] = val

            current = {k: getattr(person, k) for k in submitted}
            diff = compute_update_diff(current, submitted)
            if not diff:
                ui.notify(t("saved"), color="positive")
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
                    ok = conn.modify(person.ldap_dn, modify_payload)
                    if not ok:
                        ui.notify(
                            t("ad_write_failed", error=conn.result.get("description", "")),
                            color="negative",
                        )
                        return

            await _update_user(person.id, diff)
            from not_dot_net.backend.audit import log_audit
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
    people = await _load_people()
    updated = next((p for p in people if p.id == person.id), person)
    await _render_detail(container, updated, current_user, state)


async def _render_tenure_history(parent_container, person: User, current_user: User, is_admin: bool):
    """Render the employment history timeline for a person."""
    from not_dot_net.backend.tenure_service import list_tenures as _list_tenures

    tenures = await _list_tenures(person.id)

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
                    _render_tenure_row(ten, is_admin, refresh_tenures, person, current_user)

        if is_admin:
            async def show_add():
                await _tenure_add_dialog(person, current_user, refresh_tenures)
            ui.button(t("add_tenure"), icon="add", on_click=show_add).props("flat dense")

        await refresh_tenures()


def _render_tenure_row(tenure, is_admin: bool, on_refresh, person: User, current_user: User):
    end_label = t("tenure_current") if tenure.end_date is None else str(tenure.end_date)
    with ui.row().classes("items-center gap-2 w-full"):
        ui.chip(tenure.status, color="primary").props("dense outline")
        ui.label(f"{tenure.employer}").classes("text-sm font-medium")
        ui.label(f"{tenure.start_date} → {end_label}").classes("text-sm text-gray-500")
        if tenure.notes:
            ui.icon("info", size="xs").tooltip(tenure.notes)
        if is_admin:
            async def do_edit(t_id=tenure.id):
                await _tenure_edit_dialog(t_id, person, current_user, on_refresh)
            ui.button(icon="edit", on_click=do_edit).props("flat dense round size=xs")

            async def do_delete(t_id=tenure.id):
                from not_dot_net.backend.tenure_service import delete_tenure as _del
                await _del(t_id)
                from not_dot_net.backend.audit import log_audit
                await log_audit(
                    "user", "delete_tenure",
                    actor_id=current_user.id, actor_email=current_user.email,
                    target_type="user", target_id=person.id,
                )
                ui.notify(t("tenure_deleted"), color="positive")
                await on_refresh()
            ui.button(icon="delete", on_click=do_delete).props("flat dense round size=xs color=negative")


async def _tenure_add_dialog(person: User, current_user: User, on_refresh):
    from not_dot_net.backend.tenure_service import add_tenure as _add
    from not_dot_net.config import org_config

    cfg = await org_config.get()

    dialog = ui.dialog()
    with dialog, ui.card().classes("w-96"):
        ui.label(t("add_tenure")).classes("text-h6")
        status_input = ui.select(cfg.employment_statuses, label=t("status")).props("outlined dense stack-label")
        employer_input = ui.select(cfg.employers, label=t("employer")).props("outlined dense stack-label")
        start_input = ui.input(t("start_date"), placeholder="YYYY-MM-DD").props("outlined dense stack-label")
        end_input = ui.input(t("end_date"), placeholder="YYYY-MM-DD (optional)").props("outlined dense stack-label")
        notes_input = ui.input(t("tenure_notes")).props("outlined dense stack-label")

        async def save():
            if not status_input.value or not employer_input.value or not start_input.value:
                ui.notify(t("required_field"), color="warning")
                return
            start = date.fromisoformat(start_input.value)
            end = date.fromisoformat(end_input.value) if end_input.value else None
            await _add(
                user_id=person.id,
                status=status_input.value,
                employer=employer_input.value,
                start_date=start,
                end_date=end,
                notes=notes_input.value or None,
            )
            from not_dot_net.backend.audit import log_audit
            await log_audit(
                "user", "add_tenure",
                actor_id=current_user.id, actor_email=current_user.email,
                target_type="user", target_id=person.id,
                detail=f"status={status_input.value} employer={employer_input.value}",
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
    from not_dot_net.config import org_config

    cfg = await org_config.get()
    tenures = await _list(person.id)
    tenure = next((ten for ten in tenures if ten.id == tenure_id), None)
    if tenure is None:
        return

    dialog = ui.dialog()
    with dialog, ui.card().classes("w-96"):
        ui.label(t("edit_tenure")).classes("text-h6")
        status_input = ui.select(cfg.employment_statuses, value=tenure.status, label=t("status")).props("outlined dense stack-label")
        employer_input = ui.select(cfg.employers, value=tenure.employer, label=t("employer")).props("outlined dense stack-label")
        start_input = ui.input(t("start_date"), value=str(tenure.start_date)).props("outlined dense stack-label")
        end_input = ui.input(t("end_date"), value=str(tenure.end_date) if tenure.end_date else "").props("outlined dense stack-label")
        notes_input = ui.input(t("tenure_notes"), value=tenure.notes or "").props("outlined dense stack-label")

        async def save():
            if not status_input.value or not employer_input.value or not start_input.value:
                ui.notify(t("required_field"), color="warning")
                return
            start = date.fromisoformat(start_input.value)
            end = date.fromisoformat(end_input.value) if end_input.value else None
            await _update(
                tenure_id,
                status=status_input.value,
                employer=employer_input.value,
                start_date=start,
                end_date=end,
                notes=notes_input.value or None,
            )
            from not_dot_net.backend.audit import log_audit
            await log_audit(
                "user", "update_tenure",
                actor_id=current_user.id, actor_email=current_user.email,
                target_type="user", target_id=person.id,
                detail=f"status={status_input.value} employer={employer_input.value}",
            )
            dialog.close()
            ui.notify(t("tenure_saved"), color="positive")
            await on_refresh()

        with ui.row():
            ui.button(t("save"), on_click=save).props("flat color=primary")
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

    dialog.open()
