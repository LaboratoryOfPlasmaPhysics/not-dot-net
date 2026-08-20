"""Bespoke admin editor for reusable field definitions (Settings -> Field definitions)."""

from nicegui import ui

from not_dot_net.backend.field_definitions import (
    FieldDefinition, FieldDefinitionInUse,
    field_definitions_config,
    save_field_definition, delete_field_definition,
)
from not_dot_net.backend.permissions import check_permission
from not_dot_net.backend.vocabularies import list_vocabularies
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.workflow_editor_options import display_name_to_key

_FIELD_TYPES = ["text", "email", "phone", "textarea", "date", "select",
                "file", "location", "checkbox"]


async def render(user) -> None:
    await check_permission(user, "manage_settings")
    container = ui.column().classes("w-full")

    async def refresh():
        container.clear()
        cfg = await field_definitions_config.get()
        vocab_keys = [None, *[v.key for v in await list_vocabularies()]]
        with container:
            if not cfg.definitions:
                ui.label(t("field_defs_empty")).classes("text-grey text-sm")
            for key in sorted(cfg.definitions):
                defn = cfg.definitions[key]
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label(defn.label or defn.key).classes("font-medium")
                    ui.badge(defn.type).props("color=grey")
                    ui.button(t("edit"),
                              on_click=lambda d=defn: _open_editor(d, vocab_keys, refresh)
                              ).props("flat dense")
                    ui.button(icon="delete",
                              on_click=lambda k=key: _confirm_delete(k, refresh)
                              ).props("flat dense color=negative").tooltip(t("delete"))
            ui.button(t("field_defs_new"), icon="add",
                      on_click=lambda c=cfg: _prompt_new(c, refresh)).props("flat")

    await refresh()


def _prompt_new(cfg, on_done) -> None:
    dlg = ui.dialog()
    with dlg, ui.card():
        ui.label(t("field_defs_new"))
        name = ui.input(t("field_defs_name")).props("outlined dense")

        async def create():
            if not (name.value or "").strip():
                ui.notify(t("field_defs_name_required"), color="warning")
                return
            key = display_name_to_key(name.value, set(cfg.definitions), fallback_prefix="field")
            await save_field_definition(FieldDefinition(key=key, type="text", label=name.value))
            dlg.close()
            await on_done()

        ui.button(t("save"), on_click=create).props("color=primary")
    dlg.open()


def _confirm_delete(key: str, on_done) -> None:
    dlg = ui.dialog()
    with dlg, ui.card():
        ui.label(t("field_defs_confirm_delete", defn_key=key))

        async def do():
            try:
                await delete_field_definition(key)
            except FieldDefinitionInUse as exc:
                ui.notify(t("field_defs_in_use", usages=", ".join(exc.usages)), color="negative")
                return
            dlg.close()
            await on_done()

        with ui.row():
            ui.button(t("delete"), on_click=do).props("color=negative")
            ui.button(t("cancel"), on_click=dlg.close).props("flat")
    dlg.open()


def _open_editor(defn: FieldDefinition, vocab_keys, on_done) -> None:
    working = defn.model_copy(deep=True)
    dlg = ui.dialog()
    with dlg, ui.card().classes("w-full"):
        ui.label(working.key).classes("text-h6")
        ui.input(t("field_display_name"), value=working.label,
                 on_change=lambda e: setattr(working, "label", e.value)
                 ).props("outlined dense stack-label").classes("w-full")
        ui.select(_FIELD_TYPES, value=working.type, label=t("field_type"),
                  on_change=lambda e: setattr(working, "type", e.value)
                  ).props("outlined dense stack-label").classes("w-full")
        ui.switch(t("field_required"), value=working.required,
                  on_change=lambda e: setattr(working, "required", e.value))
        ui.switch(t("field_half_width"), value=working.half_width,
                  on_change=lambda e: setattr(working, "half_width", e.value))
        ui.switch(t("field_encrypted"), value=working.encrypted,
                  on_change=lambda e: setattr(working, "encrypted", e.value))
        ui.select(vocab_keys, value=working.options_key, label=t("field_options_key"),
                  on_change=lambda e: setattr(working, "options_key", e.value)
                  ).props("outlined dense stack-label").classes("w-full")

        async def save():
            await save_field_definition(working)
            dlg.close()
            await on_done()

        with ui.row():
            ui.button(t("save"), on_click=save).props("color=primary")
            ui.button(t("cancel"), on_click=dlg.close).props("flat")
    dlg.open()
