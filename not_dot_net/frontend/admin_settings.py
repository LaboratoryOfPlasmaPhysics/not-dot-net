"""Admin settings page — auto-generated forms from config registry."""

import json
import logging
from enum import Enum

from nicegui import ui
from pydantic import BaseModel, ValidationError
from yaml import safe_dump, safe_load

from not_dot_net.backend.app_config import get_registry
from not_dot_net.backend.audit import log_audit
from not_dot_net.backend.data_io import export_all, import_all
from not_dot_net.frontend.admin_roles import render as render_roles
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.widgets import chip_list_editor, keyed_chip_editor

logger = logging.getLogger(__name__)


def _is_enum(annotation) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, Enum)


def _is_complex(schema: type[BaseModel]) -> bool:
    """A schema is complex (needs YAML editor) only if it contains a nested
    BaseModel — directly, in a list, or as a dict value. Plain `dict[str, list[str]]`
    is editable via `keyed_chip_editor` and is NOT complex.
    """
    for field_info in schema.model_fields.values():
        annotation = field_info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return True
        args = getattr(annotation, "__args__", ())
        for arg in args:
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                return True
    return False


async def render(user):
    """Render the admin settings tab content."""
    from not_dot_net.backend.permissions import check_permission
    await check_permission(user, "manage_settings")

    with ui.expansion("Roles", icon="admin_panel_settings").classes("w-full"):
        await render_roles(user)

    with ui.expansion(t("import_export"), icon="swap_vert").classes("w-full"):
        _render_import_export(user)

    registry = get_registry()

    for prefix, cfg_section in sorted(registry.items()):
        current = await cfg_section.get()
        schema = cfg_section.schema

        with ui.expansion(cfg_section.label, icon="settings").classes("w-full"):
            if prefix == "workflows":
                from not_dot_net.frontend.workflow_editor import open_workflow_editor
                wf_count = len(current.workflows)
                step_count = sum(len(w.steps) for w in current.workflows.values())
                ui.label(f"{wf_count} workflows, {step_count} steps").classes("text-sm text-grey mb-2")
                ui.button(
                    t("edit_workflows"),
                    icon="edit",
                    on_click=lambda u=user: open_workflow_editor(u),
                ).props("color=primary")
            elif _is_complex(schema):
                await _render_yaml_editor(prefix, cfg_section, current, user)
            else:
                await _render_form(prefix, cfg_section, current, user)

            if prefix == "ldap":
                _render_ldap_sync(user)


async def _render_form(prefix, cfg_section, current, user):
    """Auto-generate form fields from Pydantic model."""
    inputs = {}
    schema = cfg_section.schema
    data = current.model_dump()

    for field_name, field_info in schema.model_fields.items():
        annotation = field_info.annotation
        value = data.get(field_name, field_info.default)

        hint = field_info.description

        if annotation is bool:
            widget = ui.switch(field_name, value=value)
        elif _is_enum(annotation):
            options = {m.value: m.value for m in annotation}
            widget = ui.select(options, label=field_name, value=value).classes("w-full")
        elif annotation is int:
            widget = ui.number(field_name, value=value)
        elif annotation is str:
            widget = ui.input(field_name, value=value).classes("w-full")
        elif annotation == list[str]:
            widget = chip_list_editor(value if isinstance(value, list) else [], label=field_name)
        elif annotation == dict[str, list[str]]:
            widget = keyed_chip_editor(value if isinstance(value, dict) else {})
        else:
            widget = ui.input(field_name, value=str(value)).classes("w-full")

        if hint:
            widget.tooltip(hint)
        inputs[field_name] = widget

    async def save():
        update = {}
        for field_name, field_info in schema.model_fields.items():
            widget = inputs[field_name]
            annotation = field_info.annotation
            if annotation is bool:
                update[field_name] = widget.value
            elif annotation is int:
                update[field_name] = int(widget.value)
            elif annotation == list[str]:
                update[field_name] = list(widget.value)
            elif annotation == dict[str, list[str]]:
                update[field_name] = widget.value
            else:
                update[field_name] = widget.value
        try:
            new_config = schema.model_validate(update)
            await cfg_section.set(new_config)
            await log_audit("settings", "update", actor_id=user.id, actor_email=user.email, detail=f"section={prefix}")
            ui.notify(t("settings_saved"), color="positive")
        except ValidationError as e:
            ui.notify(str(e), color="negative")

    async def reset():
        await cfg_section.reset()
        await log_audit("settings", "reset", actor_id=user.id, actor_email=user.email, detail=f"section={prefix}")
        ui.notify(t("settings_reset"), color="info")

    with ui.row():
        ui.button(t("save"), on_click=save).props("color=primary")
        ui.button(t("reset_defaults"), on_click=reset).props("flat color=grey")


async def _render_yaml_editor(prefix, cfg_section, current, user):
    """YAML code editor for complex config sections."""
    yaml_str = safe_dump(current.model_dump(), default_flow_style=False, allow_unicode=True)
    editor = ui.codemirror(yaml_str, language="yaml").classes("w-full").style("min-height: 300px")

    async def save():
        try:
            data = safe_load(editor.value)
            new_config = cfg_section.schema.model_validate(data)
            await cfg_section.set(new_config)
            await log_audit("settings", "update", actor_id=user.id, actor_email=user.email, detail=f"section={prefix}")
            ui.notify(t("settings_saved"), color="positive")
        except Exception as e:
            ui.notify(str(e), color="negative")

    async def reset():
        await cfg_section.reset()
        default = cfg_section.schema()
        editor.value = safe_dump(default.model_dump(), default_flow_style=False, allow_unicode=True)
        await log_audit("settings", "reset", actor_id=user.id, actor_email=user.email, detail=f"section={prefix}")
        ui.notify(t("settings_reset"), color="info")

    with ui.row():
        ui.button(t("save"), on_click=save).props("color=primary")
        ui.button(t("reset_defaults"), on_click=reset).props("flat color=grey")


def _render_import_export(user):
    ui.label(t("import_export_help")).classes("text-sm text-grey mb-2")

    with ui.row().classes("items-center gap-2"):
        async def do_export():
            data = await export_all()
            content = json.dumps(data, indent=2, ensure_ascii=False)
            ui.download(content.encode(), "not-dot-net-export.json")
            await log_audit(
                "settings", "export",
                actor_id=user.id, actor_email=user.email,
                detail=f"pages={len(data.get('pages', []))} resources={len(data.get('resources', []))}",
            )

        ui.button(t("export_all"), icon="download", on_click=do_export).props("color=primary")

    ui.separator().classes("my-3")

    replace_toggle = ui.switch(t("import_replace")).tooltip(t("import_replace_help"))

    async def handle_upload(e):
        await _handle_import_upload(e, replace=replace_toggle.value, user=user)

    ui.upload(
        label=t("import_file"),
        on_upload=handle_upload,
        auto_upload=True,
    ).props("accept=.json").classes("w-full max-w-md")


async def _handle_import_upload(e, *, replace: bool, user):
    try:
        data = await e.file.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        logger.warning("Import: invalid JSON: %s", exc)
        ui.notify(t("import_invalid_json"), color="negative")
        return
    try:
        result = await import_all(data, replace=replace)
    except Exception:
        logger.exception("Import failed")
        ui.notify(t("import_failed"), color="negative")
        return
    if not result:
        ui.notify(t("import_nothing"), color="warning")
        return
    ui.notify("; ".join(
        f"{entity}: {c['created']} created, {c['updated']} updated, {c['skipped']} skipped"
        for entity, c in result.items()
    ), color="positive", multi_line=True)
    await log_audit(
        "settings", "import",
        actor_id=user.id, actor_email=user.email,
        detail=json.dumps(result),
    )


def _render_ldap_sync(user):
    """Sync all AD users button — placed inside the LDAP settings expansion."""
    ui.separator().classes("my-3")
    ui.label(t("sync_ad_help")).classes("text-sm text-grey mb-2")

    def open_sync_dialog():
        from not_dot_net.backend.auth.ldap import sync_all_from_ldap, LdapModifyError

        dialog = ui.dialog()
        with dialog, ui.card().classes("w-96"):
            ui.label(t("sync_ad_users")).classes("text-h6")
            username_input = ui.input(t("ad_admin_username")).props("outlined dense")
            password_input = ui.input(t("password"), password=True).props("outlined dense")
            error_label = ui.label("").classes("text-negative")
            result_label = ui.label("").classes("text-sm")

            async def do_sync():
                bind_user = username_input.value.strip()
                if not bind_user or not password_input.value:
                    return
                error_label.set_text("")
                result_label.set_text(t("sync_ad_running"))
                try:
                    result = await sync_all_from_ldap(bind_user, password_input.value)
                except LdapModifyError as e:
                    msg = str(e)
                    error_label.set_text(
                        t("ad_bind_failed") if "bind" in msg.lower() else msg
                    )
                    result_label.set_text("")
                    return
                summary = t("sync_ad_result",
                            synced=result.synced,
                            provisioned=result.provisioned,
                            skipped=result.skipped)
                if result.errors:
                    summary += f"\n{t('sync_ad_errors', count=len(result.errors))}"
                result_label.set_text(summary)
                ui.notify(summary, color="positive", multi_line=True)
                await log_audit(
                    "settings", "ldap_sync",
                    actor_id=user.id, actor_email=user.email,
                    detail=f"synced={result.synced} provisioned={result.provisioned} "
                           f"skipped={result.skipped} errors={len(result.errors)}",
                )

            with ui.row():
                ui.button(t("sync_ad_users"), on_click=do_sync).props("color=primary")
                ui.button(t("cancel"), on_click=dialog.close).props("flat")

        dialog.open()

    ui.button(t("sync_ad_users"), icon="sync", on_click=open_sync_dialog).props("color=primary")
