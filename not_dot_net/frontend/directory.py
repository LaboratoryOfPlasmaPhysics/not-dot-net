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


async def _load_people() -> list[User]:
    async with session_scope() as session:
        result = await session.execute(select(User).where(User.is_active == True))  # noqa: E712
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
    search = ui.input(placeholder=t("search_placeholder")).props(
        "outlined dense clearable"
    ).classes("w-full mb-4")

    card_container = ui.element("div").classes(
        "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 w-full"
    )

    state = {"expanded_id": None, "details": {}}

    async def refresh():
        people = await _load_people()
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
        ]
    )

    with ui.card() as card:
        card._person_search_text = search_text

        header = ui.row().classes("items-center gap-3 cursor-pointer w-full")
        with header:
            ui.icon("person", size="xl").classes(
                "rounded-full bg-gray-200 p-2"
            )
            with ui.column().classes("gap-0"):
                ui.label(display_name).classes("font-bold")
                if person.team:
                    ui.label(person.team).classes("text-sm text-gray-500")
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
        if person.employment_status:
            ui.label(f"{t('status')}: {person.employment_status}").classes("text-sm")
        if person.title:
            ui.label(f"{t('title')}: {person.title}").classes("text-sm")
        if person.start_date:
            ui.label(f"{t('start_date')}: {person.start_date}").classes("text-sm")
        if person.end_date:
            ui.label(f"{t('end_date')}: {person.end_date}").classes("text-sm")

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


async def _render_edit(container, person: User, current_user: User, state: dict):
    container.clear()
    is_admin = await has_permissions(current_user, "manage_users")

    with container:
        ui.separator()

        fields = {}
        if is_admin:
            fields["full_name"] = ui.input(
                t("full_name"), value=person.full_name or ""
            ).props("outlined dense")
            fields["email"] = ui.input(
                t("email"), value=person.email
            ).props("outlined dense")
            fields["team"] = ui.input(
                t("team"), value=person.team or ""
            ).props("outlined dense")
            fields["employment_status"] = ui.input(
                t("status"), value=person.employment_status or ""
            ).props("outlined dense")
            fields["title"] = ui.input(
                t("title"), value=person.title or ""
            ).props("outlined dense")
            fields["start_date"] = ui.input(
                t("start_date"), value=str(person.start_date) if person.start_date else ""
            ).props("outlined dense")
            fields["end_date"] = ui.input(
                t("end_date"), value=str(person.end_date) if person.end_date else ""
            ).props("outlined dense")

        fields["phone"] = ui.input(
            t("phone"), value=person.phone or ""
        ).props("outlined dense")
        fields["office"] = ui.input(
            t("office"), value=person.office or ""
        ).props("outlined dense")

        async def save():
            submitted = {}
            for k, v in fields.items():
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

            if not needs_ad_write:
                await _update_user(person.id, diff)
                await _finish_save(container, person, current_user, state)
                return

            is_own = person.id == current_user.id
            await _prompt_ad_credentials_and_save(
                container, person, current_user, state,
                diff, ad_changes, local_updates, is_own=is_own,
            )

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


async def _prompt_ad_credentials_and_save(
    container, person, current_user, state,
    diff, ad_changes, local_updates, *, is_own,
):
    from not_dot_net.backend.auth.ldap import (
        ldap_config, get_ldap_connect, ldap_modify_user, LdapModifyError,
    )

    dialog = ui.dialog()
    with dialog, ui.card():
        if is_own:
            ui.label(t("confirm_password_to_save_ad"))
            username_input = None
            password_input = ui.input(t("password"), password=True).props("outlined dense")
        else:
            ui.label(t("admin_ad_credentials"))
            username_input = ui.input(t("ad_admin_username")).props("outlined dense")
            password_input = ui.input(t("password"), password=True).props("outlined dense")
        error_label = ui.label("").classes("text-negative")

        async def submit():
            bind_user = (current_user.email.split("@")[0] if is_own else username_input.value)
            if not bind_user or not password_input.value:
                return
            cfg = await ldap_config.get()
            try:
                ldap_modify_user(
                    dn=person.ldap_dn,
                    changes=ad_changes,
                    bind_username=bind_user,
                    bind_password=password_input.value,
                    ldap_cfg=cfg,
                    connect=get_ldap_connect(),
                )
            except LdapModifyError as e:
                msg = str(e)
                error_label.set_text(
                    t("ad_bind_failed") if "bind" in msg.lower() else t("ad_write_failed", error=msg)
                )
                return
            await _update_user(person.id, diff)
            dialog.close()
            await _finish_save(container, person, current_user, state)

        with ui.row():
            ui.button(t("save"), on_click=submit).props("flat color=primary")
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

    dialog.open()
