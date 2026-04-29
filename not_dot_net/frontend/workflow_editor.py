"""Master-detail dialog for editing the workflows config section."""

from __future__ import annotations

import logging

from nicegui import ui
from pydantic import ValidationError

from not_dot_net.backend.audit import log_audit
from not_dot_net.backend.workflow_service import workflows_config, WorkflowsConfig
from not_dot_net.frontend.i18n import t

logger = logging.getLogger(__name__)


class WorkflowEditorDialog:
    """Dialog state holder. Construct with `await WorkflowEditorDialog.create(user)`."""

    def __init__(self, user, original: WorkflowsConfig):
        self.user = user
        self.original = original
        self.working_copy = original.model_copy(deep=True)
        self.selected_workflow: str | None = next(iter(self.working_copy.workflows), None)
        self.selected_step: str | None = None
        self.dialog: ui.dialog | None = None
        self._tree_container: ui.column | None = None
        self._detail_container: ui.column | None = None

    @classmethod
    async def create(cls, user) -> "WorkflowEditorDialog":
        original = await workflows_config.get()
        instance = cls(user, original)
        instance._build()
        return instance

    def _build(self) -> None:
        self.dialog = ui.dialog().props("maximized")
        with self.dialog, ui.card().classes("w-full h-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(t("workflows_editor")).classes("text-h6")
            with ui.row().classes("w-full grow no-wrap"):
                self._tree_container = ui.column().classes("w-72 q-pr-md").style("border-right: 1px solid #e0e0e0")
                self._detail_container = ui.column().classes("grow")
            with self._tree_container:
                ui.label("(tree placeholder)")
            with self._detail_container:
                ui.label("(detail placeholder)")
            with ui.row().classes("w-full justify-end"):
                ui.button(t("cancel"), on_click=self.close).props("flat")
                ui.button(t("reset_defaults"), on_click=self.reset).props("flat color=grey")
                ui.button(t("save"), on_click=self.save).props("color=primary")

    def open(self) -> None:
        if self.dialog:
            self.dialog.open()

    def close(self) -> None:
        if self.dialog:
            self.dialog.close()

    async def save(self) -> None:
        try:
            validated = WorkflowsConfig.model_validate(self.working_copy.model_dump())
        except ValidationError as e:
            ui.notify(str(e), color="negative", multi_line=True)
            return
        await workflows_config.set(validated)
        await log_audit(
            "settings", "update",
            actor_id=self.user.id, actor_email=self.user.email,
            detail="section=workflows",
        )
        ui.notify(t("settings_saved"), color="positive")
        self.close()

    async def reset(self) -> None:
        await workflows_config.reset()
        self.original = await workflows_config.get()
        self.working_copy = self.original.model_copy(deep=True)
        ui.notify(t("settings_reset"), color="info")


async def open_workflow_editor(user) -> None:
    dlg = await WorkflowEditorDialog.create(user)
    dlg.open()
