"""Admin roles management UI — create, edit, delete roles and assign permissions."""

from nicegui import ui
from sqlalchemy import select, func


from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import check_permission, get_permissions, MANAGE_ROLES
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.widgets import confirm_dialog


async def render(user):
    """Render the roles management section."""
    await check_permission(user, MANAGE_ROLES)

    container = ui.column().classes("w-full")

    async def refresh():
        await _render_roles(container, user)

    ui.timer(0, refresh, once=True)


async def _user_count_by_role() -> dict[str, int]:
    async with session_scope() as session:
        result = await session.execute(
            select(User.role, func.count()).group_by(User.role)
        )
        return {row[0]: row[1] for row in result.all()}


async def _render_roles(container, user):
    container.clear()
    cfg = await roles_config.get()
    all_perms = get_permissions()
    user_counts = await _user_count_by_role()

    with container:
        ui.label(t("roles")).classes("text-h6 mb-2")

        for role_key, role_def in sorted(cfg.roles.items()):
            count = user_counts.get(role_key, 0)
            with ui.expansion(
                f"{role_def.label} ({role_key}) — {count} users, {len(role_def.permissions)} permissions"
            ).classes("w-full"):
                await _render_role_editor(container, user, role_key, role_def, all_perms, count)

        # Add role button
        with ui.row().classes("mt-3 gap-2"):
            new_key = ui.input(t("role_key")).props("outlined dense").classes("w-32")
            new_label = ui.input(t("role_label")).props("outlined dense").classes("w-48")

            async def add_role():
                try:
                    await check_permission(user, MANAGE_ROLES)
                except PermissionError:
                    ui.notify(t("permission_denied"), color="negative")
                    return
                key = new_key.value.strip().lower()
                label = new_label.value.strip()
                if not key or not label:
                    ui.notify("Key and label required", color="negative")
                    return
                cfg_now = await roles_config.get()
                if key in cfg_now.roles:
                    ui.notify(f"Role '{key}' already exists", color="negative")
                    return
                cfg_now.roles[key] = RoleDefinition(label=label, permissions=[])
                await roles_config.set(cfg_now)
                ui.notify(f"Role '{key}' created", color="positive")
                await _render_roles(container, user)

            ui.button(t("add"), icon="add", on_click=add_role).props("flat color=primary")

        # Default role selector
        ui.label(t("default_role")).classes("text-subtitle2 mt-4")
        role_options = [""] + sorted(cfg.roles.keys())
        default_select = ui.select(
            options=role_options,
            value=cfg.default_role,
            label=t("default_role"),
        ).props("outlined dense").classes("w-48")

        async def save_default():
            try:
                await check_permission(user, MANAGE_ROLES)
            except PermissionError:
                ui.notify(t("permission_denied"), color="negative")
                return
            cfg_now = await roles_config.get()
            cfg_now.default_role = default_select.value
            await roles_config.set(cfg_now)
            ui.notify(t("settings_saved"), color="positive")

        ui.button(t("save"), on_click=save_default).props("flat color=primary")


async def _render_role_editor(outer_container, user, role_key, role_def, all_perms, user_count):
    """Render permission checkboxes for a single role."""
    checkboxes = {}
    with ui.element("div").classes("grid grid-cols-2 md:grid-cols-3 gap-2"):
        for perm_key, perm_info in sorted(all_perms.items()):
            checked = perm_key in role_def.permissions
            cb = ui.checkbox(
                f"{perm_info.label}",
                value=checked,
            ).tooltip(perm_info.description or perm_key)
            checkboxes[perm_key] = cb

    with ui.row().classes("mt-2 gap-2"):
        async def save():
            try:
                await check_permission(user, MANAGE_ROLES)
            except PermissionError:
                ui.notify(t("permission_denied"), color="negative")
                return
            cfg = await roles_config.get()
            selected = [k for k, cb in checkboxes.items() if cb.value]
            cfg.roles[role_key].permissions = selected
            await roles_config.set(cfg)

            from not_dot_net.backend.audit import log_audit
            await log_audit(
                "settings", "update_role",
                actor_id=user.id, actor_email=user.email,
                detail=f"role={role_key} permissions={selected}",
            )
            ui.notify(t("settings_saved"), color="positive")

        ui.button(t("save"), on_click=save).props("color=primary")

        async def delete():
            try:
                await check_permission(user, MANAGE_ROLES)
            except PermissionError:
                ui.notify(t("permission_denied"), color="negative")
                return
            current_count = (await _user_count_by_role()).get(role_key, 0)
            if current_count > 0:
                ui.notify(
                    t("role_delete_in_use", key=role_key, count=current_count),
                    color="negative",
                )
                return
            cfg = await roles_config.get()
            del cfg.roles[role_key]
            await roles_config.set(cfg)
            ui.notify(t("role_deleted", key=role_key), color="positive")
            await _render_roles(outer_container, user)

        delete_dlg = confirm_dialog(
            t("confirm_delete_role", key=role_key), delete, confirm_label=t("delete"),
        )
        ui.button(t("delete"), icon="delete", on_click=delete_dlg.open).props(
            "flat color=negative"
        )
