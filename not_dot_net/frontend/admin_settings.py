"""Admin settings page — auto-generated forms from config registry."""

from nicegui import ui
from pydantic import BaseModel, ValidationError
from yaml import safe_dump, safe_load

from not_dot_net.backend.app_config import get_registry
from not_dot_net.backend.audit import log_audit
from not_dot_net.frontend.admin_roles import render as render_roles
from not_dot_net.frontend.i18n import t


def _is_complex(schema: type[BaseModel]) -> bool:
    """Check if a schema has nested models or dicts — use YAML editor."""
    for field_info in schema.model_fields.values():
        annotation = field_info.annotation
        if annotation is dict or (hasattr(annotation, "__origin__") and annotation.__origin__ is dict):
            return True
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return True
    return False


async def render(user):
    """Render the admin settings tab content."""
    from not_dot_net.backend.permissions import check_permission
    await check_permission(user, "manage_settings")

    with ui.expansion("Roles", icon="admin_panel_settings").classes("w-full"):
        await render_roles(user)

    registry = get_registry()

    for prefix, cfg_section in sorted(registry.items()):
        current = await cfg_section.get()
        schema = cfg_section.schema

        with ui.expansion(cfg_section.label, icon="settings").classes("w-full"):
            if _is_complex(schema):
                await _render_yaml_editor(prefix, cfg_section, current, user)
            else:
                await _render_form(prefix, cfg_section, current, user)


async def _render_form(prefix, cfg_section, current, user):
    """Auto-generate form fields from Pydantic model."""
    inputs = {}
    schema = cfg_section.schema
    data = current.model_dump()

    for field_name, field_info in schema.model_fields.items():
        annotation = field_info.annotation
        value = data.get(field_name, field_info.default)

        if annotation is bool:
            inputs[field_name] = ui.switch(field_name, value=value)
        elif annotation is int:
            inputs[field_name] = ui.number(field_name, value=value)
        elif annotation is str:
            inputs[field_name] = ui.input(field_name, value=value).classes("w-full")
        elif annotation == list[str]:
            inputs[field_name] = ui.input(
                field_name,
                value=", ".join(value) if isinstance(value, list) else str(value),
            ).classes("w-full").tooltip("Comma-separated values")
        else:
            inputs[field_name] = ui.input(field_name, value=str(value)).classes("w-full")

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
                update[field_name] = [s.strip() for s in widget.value.split(",") if s.strip()]
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
