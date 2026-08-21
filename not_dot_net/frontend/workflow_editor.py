"""Master-detail dialog for editing the workflows config section."""

from __future__ import annotations

import logging
import re

from nicegui import ui
from pydantic import ValidationError
from yaml import safe_dump, safe_load

from not_dot_net.backend.audit import log_audit
from not_dot_net.backend.permissions import get_permissions
from not_dot_net.backend.roles import roles_config
from not_dot_net.backend.workflow_config import workflows_config, WorkflowsConfig
from not_dot_net.config import FieldConfig, FieldRef, NotificationRuleConfig, StepEffectConfig, WorkflowConfig, WorkflowStepConfig, resolve_field_ref
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.errors import notify_error
from not_dot_net.frontend.widgets import confirm_dialog
from not_dot_net.frontend.workflow_editor_options import (
    assignee_options,
    assignee_summary,
    display_name_to_key,
    event_options,
    recipient_options,
    _slugify,
)

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_slug(key: str) -> None:
    if not _SLUG_RE.fullmatch(key):
        raise ValueError(
            f"Invalid key '{key}': must be lowercase letters, digits, underscore; start with a letter"
        )


def _current_assignee_value(step) -> str | None:
    """Map a WorkflowStepConfig's assignee fields back to a picker value."""
    if step.assignee_role:
        return f"role:{step.assignee_role}"
    if step.assignee_permission:
        return f"permission:{step.assignee_permission}"
    if step.assignee:
        return f"contextual:{step.assignee}"
    return None


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
        self._workflow_doc_instructions_widget = None
        self._yaml_editor = None
        self._active_tab = "Form"
        self._body_container = None
        self._save_button = None
        self._warnings_label = None
        self._current_warnings: list[str] = []
        self._roles: dict = {}
        self._permissions: dict = {}
        self._eligible_groups_snapshot: dict[str, str] = {}
        self._unlocked_fields: set[tuple[str, str, str]] = set()
        self._save_dirty_badge = None
        self._field_defs: dict = {}

    @classmethod
    async def create(cls, user) -> "WorkflowEditorDialog":
        original = await workflows_config.get()
        instance = cls(user, original)
        roles_cfg = await roles_config.get()
        instance._roles = dict(roles_cfg.roles)
        instance._permissions = dict(get_permissions())
        from not_dot_net.backend.ad_account_config import ad_account_config
        cfg = await ad_account_config.get()
        instance._eligible_groups_snapshot = {dn: dn for dn in cfg.eligible_groups}
        from not_dot_net.backend.vocabularies import list_vocabularies
        instance._vocab_keys = [v.key for v in await list_vocabularies()]
        from not_dot_net.backend.field_definitions import field_definitions_config
        instance._field_defs = dict((await field_definitions_config.get()).definitions)
        instance._build()
        return instance

    def _build(self) -> None:
        self.dialog = ui.dialog().props("maximized")
        with self.dialog, ui.card().classes("w-full h-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(t("workflows_editor")).classes("text-h6")
                ui.button(icon="code", on_click=self._open_yaml_view
                          ).props("flat dense").tooltip(t("yaml_advanced"))
            self._body_container = ui.column().classes("w-full grow")
            with ui.row().classes("w-full justify-between items-center"):
                self._warnings_label = ui.label("").classes("text-warning text-sm")
                self._warnings_label.on("click", lambda e: self._show_warnings(self._current_warnings))
                with ui.row():
                    ui.button(t("cancel"), on_click=self._on_cancel_click).props("flat")
                    reset_dlg = confirm_dialog(
                        t("confirm_reset_workflows"), self.reset,
                        confirm_label=t("reset_defaults"), confirm_icon="restart_alt",
                    )
                    ui.button(t("reset_defaults"), on_click=reset_dlg.open).props("flat color=grey")
                    self._save_button = ui.button(t("save"), on_click=self.save).props("color=primary")
                    with self._save_button:
                        self._save_dirty_badge = ui.badge(color="warning", text_color="white"
                                                          ).props("floating rounded")
                        self._save_dirty_badge.text = "•"
                        self._save_dirty_badge.tooltip(t("save_dirty_tooltip"))
                        self._save_dirty_badge.visible = False
        self._render_form_body()

    def _render_form_body(self) -> None:
        self._active_tab = "Form"
        self._body_container.clear()
        with self._body_container:
            with ui.row().classes("w-full grow no-wrap"):
                self._tree_container = ui.column().classes("w-72 q-pr-md").style("border-right: 1px solid #e0e0e0")
                self._detail_container = ui.column().classes("grow")
        self._refresh_tree()
        self._refresh_detail()

    def _open_yaml_view(self) -> None:
        # Capture form-side state into the model before rendering YAML.
        self._collect_widget_state()
        self._active_tab = "YAML"
        self._body_container.clear()
        with self._body_container:
            with ui.row().classes("w-full justify-between items-center"):
                ui.button(t("yaml_back_to_form"), icon="arrow_back",
                          on_click=self._close_yaml_view).props("flat")
            self._yaml_editor = ui.codemirror(self.dump_yaml(), language="yaml"
                                              ).classes("w-full grow").style("min-height: 0")

    def _close_yaml_view(self) -> None:
        if self._yaml_editor is not None:
            try:
                self.apply_yaml(self._yaml_editor.value)
            except ValueError as err:
                ui.notify(t("invalid_yaml", error=err), color="negative", multi_line=True)
                return  # stay on YAML so user can fix
        self._yaml_editor = None
        self._render_form_body()

    # --- workflow mutations ---

    def add_workflow(self, key: str, label: str | None = None) -> None:
        _validate_slug(key)
        if key in self.working_copy.workflows:
            raise ValueError(f"Workflow '{key}' already exists")
        self.working_copy.workflows[key] = WorkflowConfig(label=label or key, steps=[])
        self.selected_workflow = key
        self.selected_step = None
        self._refresh_tree()
        self._refresh_detail()

    def delete_workflow(self, key: str) -> None:
        if key not in self.working_copy.workflows:
            return
        del self.working_copy.workflows[key]
        if self.selected_workflow == key:
            self.selected_workflow = next(iter(self.working_copy.workflows), None)
            self.selected_step = None
        self._refresh_tree()
        self._refresh_detail()

    def duplicate_workflow(self, src_key: str, new_key: str, label: str | None = None) -> None:
        _validate_slug(new_key)
        if new_key in self.working_copy.workflows:
            raise ValueError(f"Workflow '{new_key}' already exists")
        if src_key not in self.working_copy.workflows:
            raise ValueError(f"Workflow '{src_key}' does not exist")
        self.working_copy.workflows[new_key] = self.working_copy.workflows[src_key].model_copy(deep=True)
        if label:
            self.working_copy.workflows[new_key].label = label
        self.selected_workflow = new_key
        self.selected_step = None
        self._refresh_tree()
        self._refresh_detail()

    # --- step mutations ---

    def add_step(self, wf_key: str, step_key: str, label: str = "") -> None:
        _validate_slug(step_key)
        wf = self.working_copy.workflows[wf_key]
        if any(s.key == step_key for s in wf.steps):
            raise ValueError(f"Step '{step_key}' already exists in workflow '{wf_key}'")
        wf.steps.append(WorkflowStepConfig(key=step_key, label=label, type="form"))
        self.selected_workflow = wf_key
        self.selected_step = step_key
        self._refresh_tree()
        self._refresh_detail()

    def delete_step(self, wf_key: str, step_key: str) -> None:
        wf = self.working_copy.workflows[wf_key]
        wf.steps = [s for s in wf.steps if s.key != step_key]
        if self.selected_step == step_key:
            self.selected_step = wf.steps[0].key if wf.steps else None
        self._refresh_tree()
        self._refresh_detail()

    def move_step(self, wf_key: str, step_key: str, delta: int) -> None:
        wf = self.working_copy.workflows[wf_key]
        keys = [s.key for s in wf.steps]
        if step_key not in keys:
            raise KeyError(f"step {step_key} not found in {wf_key}")
        idx = keys.index(step_key)
        new_idx = idx + delta
        if not 0 <= new_idx < len(wf.steps):
            return
        wf.steps[idx], wf.steps[new_idx] = wf.steps[new_idx], wf.steps[idx]
        self._refresh_tree()
        self._refresh_detail()

    def select(self, wf_key: str, step_key: str | None = None) -> None:
        self.selected_workflow = wf_key
        self.selected_step = step_key
        self._refresh_tree()
        self._refresh_detail()

    # --- workflow-level field mutations ---

    def set_workflow_label(self, wf_key: str, value: str) -> None:
        self.working_copy.workflows[wf_key].label = value

    def set_workflow_field(self, wf_key: str, field: str, value) -> None:
        setattr(self.working_copy.workflows[wf_key], field, value)

    def add_notification_rule(self, wf_key: str) -> None:
        self.working_copy.workflows[wf_key].notifications.append(
            NotificationRuleConfig(event="", step=None, notify=[])
        )
        self._refresh_detail()

    def delete_notification_rule(self, wf_key: str, index: int) -> None:
        del self.working_copy.workflows[wf_key].notifications[index]
        self._refresh_detail()

    def set_notification_event(self, wf_key: str, index: int, value: str) -> None:
        self.working_copy.workflows[wf_key].notifications[index].event = value

    def set_notification_step(self, wf_key: str, index: int, value: str | None) -> None:
        self.working_copy.workflows[wf_key].notifications[index].step = value

    def set_notification_recipients(self, wf_key: str, index: int, values: list[str]) -> None:
        self.working_copy.workflows[wf_key].notifications[index].notify = list(values)

    # --- step-level field mutations ---

    def _find_step(self, wf_key: str, step_key: str):
        wf = self.working_copy.workflows[wf_key]
        for step in wf.steps:
            if step.key == step_key:
                return step
        raise KeyError(f"step {step_key} not found in {wf_key}")

    def set_step_field(self, wf_key: str, step_key: str, field: str, value) -> None:
        step = self._find_step(wf_key, step_key)
        if field == "key":
            wf = self.working_copy.workflows[wf_key]
            if any(s.key == value for s in wf.steps if s is not step):
                raise ValueError(f"Step '{value}' already exists in workflow '{wf_key}'")
            _validate_slug(value)
            step.key = value
            if self.selected_step == step_key:
                self.selected_step = value
            self._refresh_tree()
            self._refresh_detail()
            return
        setattr(step, field, value)

    def set_step_assignee(self, wf_key: str, step_key: str, *, mode: str, value: str | None) -> None:
        step = self._find_step(wf_key, step_key)
        step.assignee_role = None
        step.assignee_permission = None
        step.assignee = None
        if mode == "role":
            step.assignee_role = value
        elif mode == "permission":
            step.assignee_permission = value
        elif mode == "contextual":
            step.assignee = value
        else:
            raise ValueError(f"Unknown assignee mode: {mode}")

    def set_step_assignee_from_picker(self, wf_key: str, step_key: str, value: str | None) -> None:
        """Apply an assignee picker value (e.g. 'role:admin') to the step."""
        step = self._find_step(wf_key, step_key)
        step.assignee_role = None
        step.assignee_permission = None
        step.assignee = None
        if value is None:
            return
        kind, _, raw = value.partition(":")
        if kind == "role":
            step.assignee_role = raw
        elif kind == "permission":
            step.assignee_permission = raw
        elif kind == "contextual":
            step.assignee = raw
        else:
            raise ValueError(f"Unknown assignee picker value: {value}")

    # --- field-level mutations ---

    def add_field(self, wf_key: str, step_key: str) -> None:
        step = self._find_step(wf_key, step_key)
        step.fields.append(FieldConfig(name="", type="text"))
        self._refresh_detail()

    def add_field_ref(self, wf_key: str, step_key: str, ref_key: str) -> None:
        step = self._find_step(wf_key, step_key)
        step.fields.append(FieldRef(ref=ref_key))
        self._refresh_detail()

    def set_field_ref_override(self, wf_key: str, step_key: str, index: int,
                               attr: str, value) -> None:
        step = self._find_step(wf_key, step_key)
        item = step.fields[index]
        if not isinstance(item, FieldRef):
            raise ValueError("not a field reference")
        setattr(item, attr, value)   # value=None clears the override

    def set_field_attr(self, wf_key: str, step_key: str, index: int, attr: str, value) -> None:
        step = self._find_step(wf_key, step_key)
        field = step.fields[index]
        if attr == "name" and self._is_field_name_locked(wf_key, step_key, field.name):
            raise ValueError(
                f"Internal name '{field.name}' is locked; call unlock_field_name() first"
            )
        setattr(field, attr, value)

    def _is_field_saved(self, wf_key: str, step_key: str, field_name: str) -> bool:
        wf = self.original.workflows.get(wf_key)
        if not wf:
            return False
        for step in wf.steps:
            if step.key != step_key:
                continue
            for f in step.fields:
                if isinstance(f, FieldConfig) and f.name == field_name:
                    return True
        return False

    def _is_field_name_locked(self, wf_key: str, step_key: str, field_name: str) -> bool:
        if (wf_key, step_key, field_name) in self._unlocked_fields:
            return False
        return self._is_field_saved(wf_key, step_key, field_name)

    def unlock_field_name(self, wf_key: str, step_key: str, field_name: str) -> None:
        """Unlock a saved field's internal name so it can be renamed."""
        self._unlocked_fields.add((wf_key, step_key, field_name))

    def set_field_label_with_autoslug(self, wf_key: str, step_key: str, index: int, label: str) -> None:
        """Set a field's display name. Auto-generate the internal name when it's
        a new (not-yet-saved) field; leave the internal name alone for saved fields.
        """
        step = self._find_step(wf_key, step_key)
        field = step.fields[index]
        field.label = label
        if self._is_field_name_locked(wf_key, step_key, field.name):
            return
        current_name = field.name if hasattr(field, "name") else None
        taken = {f.name for f in self._resolved_step_fields(step) if f.name and f.name != current_name}
        field.name = _slugify(label, taken)

    def delete_field(self, wf_key: str, step_key: str, index: int) -> None:
        step = self._find_step(wf_key, step_key)
        del step.fields[index]
        self._refresh_detail()

    def move_field(self, wf_key: str, step_key: str, index: int, delta: int) -> None:
        step = self._find_step(wf_key, step_key)
        new_idx = index + delta
        if not 0 <= new_idx < len(step.fields):
            return
        step.fields[index], step.fields[new_idx] = step.fields[new_idx], step.fields[index]
        self._refresh_detail()

    # --- rendering ---

    def _refresh_tree(self) -> None:
        if self._tree_container is None:
            return
        self._current_warnings = self.compute_warnings()
        self._tree_container.clear()
        with self._tree_container:
            for wf_key, wf in self.working_copy.workflows.items():
                self._render_workflow_row(wf_key, wf)
            ui.button(f"+ {t('add_workflow')}", on_click=self._on_add_workflow_click
                      ).props("flat dense color=primary")

    def _render_workflow_row(self, wf_key: str, wf) -> None:
        is_selected = self.selected_workflow == wf_key
        with ui.row().classes(f"w-full items-center no-wrap {'bg-blue-1' if is_selected else ''}"):
            ui.button(wf.label or wf_key, on_click=lambda k=wf_key: self.select(k)
                      ).props("flat dense no-caps").classes("grow text-left")
            wf_warns = self.warnings_for(wf_key)
            if wf_warns:
                ui.icon("warning", color="warning", size="xs").tooltip("\n".join(wf_warns))
            ui.button(icon="content_copy", on_click=lambda k=wf_key: self._on_duplicate_click(k)
                      ).props("flat dense round size=sm").tooltip(t("duplicate"))
            ui.button(icon="delete", on_click=lambda k=wf_key: self._confirm_delete_workflow(k)
                      ).props("flat dense round size=sm color=negative")

    def _refresh_detail(self) -> None:
        if self._detail_container is None:
            return
        self._collect_widget_state()
        self._current_warnings = self.compute_warnings()
        self._workflow_doc_instructions_widget = None
        self._detail_container.clear()
        with self._detail_container:
            if self.selected_workflow is None:
                ui.label(t("no_workflow_selected")).classes("text-grey")
                return
            wf = self.working_copy.workflows[self.selected_workflow]
            if self.selected_step is None:
                self._render_workflow_editor(self.selected_workflow, wf)
            else:
                step = self._find_step(self.selected_workflow, self.selected_step)
                self._render_step_editor(self.selected_workflow, step)
        if self._warnings_label is not None:
            if self._current_warnings:
                self._warnings_label.set_text(t("warnings_count", count=len(self._current_warnings)))
                self._warnings_label.classes(replace="text-warning text-sm cursor-pointer")
            else:
                self._warnings_label.set_text("")
                self._warnings_label.classes(replace="text-warning text-sm")
        self._update_save_dirty_indicator()

    def _render_workflow_editor(self, wf_key: str, wf) -> None:
        from not_dot_net.frontend.widgets import keyed_chip_editor

        ui.label(wf.label or wf_key).classes("text-h6")

        # --- Section 1: Basics (open by default) ---
        with ui.expansion(t("wf_section_basics"), value=True, icon="info").classes("w-full"):
            ui.input(t("wf_label"), value=wf.label,
                     on_change=lambda e, k=wf_key: self.set_workflow_label(k, e.value)
                     ).classes("w-full").props("dense outlined stack-label").tooltip(t("wf_label_help"))

            with ui.expansion(t("wf_section_about_other"), icon="person_search").classes("w-full"):
                field_names = sorted({f.name for s in wf.steps
                                      for f in self._resolved_step_fields(s) if f.name})
                if field_names:
                    options = {None: "(none)", **{n: n for n in field_names}}
                    ui.select(options, value=wf.target_email_field,
                              label=t("wf_target_email"),
                              on_change=lambda e, k=wf_key: self.set_workflow_field(k, "target_email_field", e.value or None)
                              ).classes("w-full").props("dense outlined stack-label").tooltip(t("wf_target_email_help"))
                else:
                    ui.input(t("wf_target_email"), value=wf.target_email_field or "",
                             on_change=lambda e, k=wf_key: self.set_workflow_field(k, "target_email_field", e.value or None)
                             ).classes("w-full").props("dense outlined stack-label").tooltip(t("wf_target_email_help"))

        # --- Section 2: Steps pipeline ---
        ui.label(t("wf_section_steps")).classes("text-subtitle2 q-mt-md")
        self._render_pipeline(wf_key, wf)

        # --- Section 3: Notifications (collapsed; show count) ---
        n_notif = len(wf.notifications)
        with ui.expansion(f"{t('wf_section_notifications')}  ({n_notif})", icon="notifications"
                          ).classes("w-full"):
            self._render_notification_table(wf_key, wf)

        # --- Section 3: Document instructions (collapsed; show count) ---
        n_di = len(wf.document_instructions or {})
        with ui.expansion(f"{t('wf_section_doc_instructions')}  ({n_di})", icon="description"
                          ).classes("w-full"):
            di = keyed_chip_editor(wf.document_instructions or {}, key_label="status")
            self._workflow_doc_instructions_widget = (wf_key, di)

    def _render_pipeline(self, wf_key: str, wf) -> None:
        from not_dot_net.config import step_display
        if not wf.steps:
            ui.label(t("empty_steps")).classes("text-grey text-sm")
        last = len(wf.steps) - 1
        for idx, step in enumerate(wf.steps):
            card = ui.card().classes("w-full q-pa-sm cursor-pointer").props("flat bordered")
            card.on("click", lambda e, w=wf_key, s=step.key: self.select(w, s))
            with card:
                with ui.row().classes("w-full items-center no-wrap gap-2"):
                    ui.badge(str(idx + 1)).props("rounded color=primary")
                    with ui.column().classes("grow gap-0"):
                        ui.label(step_display(step)).classes("text-subtitle2")
                        ui.label(assignee_summary(step, self._roles, self._permissions)
                                 ).classes("text-grey text-xs")
                    step_warns = self.warnings_for(wf_key, step.key)
                    if step_warns:
                        ui.icon("warning", color="warning").tooltip("\n".join(step_warns))
                    up = ui.button(icon="keyboard_arrow_up").props("flat dense round size=sm")
                    up.on("click.stop", lambda e, w=wf_key, s=step.key: self.move_step(w, s, -1))
                    if idx == 0:
                        up.props("disable")
                    down = ui.button(icon="keyboard_arrow_down").props("flat dense round size=sm")
                    down.on("click.stop", lambda e, w=wf_key, s=step.key: self.move_step(w, s, +1))
                    if idx == last:
                        down.props("disable")
                    rm = ui.button(icon="delete").props("flat dense round size=sm color=negative")
                    rm.on("click.stop", lambda e, w=wf_key, s=step.key: self._confirm_delete_step(w, s))
            self._render_transition_labels(wf, idx, step)
        ui.button(f"+ {t('add_step')}", on_click=lambda k=wf_key: self._on_add_step_click(k)
                  ).props("flat dense color=primary")

    def _render_transition_labels(self, wf, idx: int, step) -> None:
        from not_dot_net.config import step_display
        actions = step.actions or []
        advancing = [a for a in actions if a not in ("reject", "request_corrections")]
        parts: list[str] = []
        if advancing:
            dest = (step_display(wf.steps[idx + 1]) if idx + 1 < len(wf.steps)
                    else f"✓ {t('pipeline_completed')}")
            parts.append(f"↓ {' / '.join(advancing)} → {dest}")
        if "request_corrections" in actions:
            target_step = next((s for s in wf.steps if s.key == step.corrections_target), None)
            dest = step_display(target_step) if target_step else (step.corrections_target or "?")
            parts.append(f"↰ request_corrections → {dest}")
        if "reject" in actions:
            parts.append(f"✕ reject → {t('pipeline_rejected')}")
        if not parts:
            parts.append(f"⚠ {t('pipeline_no_actions')}")
        with ui.row().classes("w-full q-pl-lg q-py-none"):
            ui.label("    ".join(parts)).classes("text-grey text-xs")

    def _render_notification_table(self, wf_key: str, wf) -> None:
        step_keys = [s.key for s in wf.steps]
        event_opts = {o["value"]: o["label"] for o in event_options()}

        # Recipient options for this snapshot.
        recip_opts = recipient_options(self._roles, self._permissions)
        recip_value_to_label = {o["value"]: o["label"] for o in recip_opts}

        if not wf.notifications:
            ui.label(t("empty_notifications")).classes("text-grey text-sm")

        for idx, rule in enumerate(wf.notifications):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.select(
                    options=event_opts,
                    value=rule.event or None,
                    label=t("notification_event"),
                    on_change=lambda e, i=idx, k=wf_key: self.set_notification_event(k, i, e.value or ""),
                ).props("dense outlined stack-label").classes("w-44")

                step_choices = {None: t("any_step"), **{sk: sk for sk in step_keys}}
                ui.select(
                    options=step_choices,
                    value=rule.step,
                    label=t("notification_step"),
                    on_change=lambda e, i=idx, k=wf_key: self.set_notification_step(k, i, e.value),
                ).props("dense outlined stack-label").classes("w-40")

                # Show "Unknown: <raw>" for any persisted value not in current options.
                missing = [v for v in (rule.notify or []) if v not in recip_value_to_label]
                effective_options = {o["value"]: o["label"] for o in recip_opts}
                for v in missing:
                    effective_options[v] = t("unknown_value", value=v)

                recip_select = ui.select(
                    options=effective_options,
                    value=list(rule.notify or []),
                    multiple=True,
                    label=t("notification_recipients"),
                ).props("dense outlined stack-label use-chips").classes("grow")

                def _bind_recip(w=recip_select, i=idx, k=wf_key):
                    self.set_notification_recipients(k, i, list(w.value or []))
                recip_select.on_value_change(lambda e, _b=_bind_recip: _b())

                ui.button(icon="delete",
                          on_click=lambda i=idx, k=wf_key: self.delete_notification_rule(k, i)
                          ).props("flat dense round color=negative")

        ui.button(t("add_notification_rule"),
                  on_click=lambda k=wf_key: self.add_notification_rule(k)
                  ).props("flat dense color=primary")

    def _render_effects_table(self, wf_key: str, step) -> None:
        from not_dot_net.frontend.widgets import chip_list_editor
        from not_dot_net.frontend.workflow_editor_options import effect_kind_options

        kind_opts = {o["value"]: o["label"] for o in effect_kind_options()}
        action_opts = {a: a for a in (step.actions or [])}

        if not step.effects:
            ui.label(t("empty_effects")).classes("text-grey text-sm")

        for idx, effect in enumerate(step.effects):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.select(
                    options=action_opts, value=effect.on_action, label=t("on_action"),
                    on_change=lambda e, i=idx, k=wf_key, sk=step.key:
                        self.set_effect_field(k, sk, i, "on_action", e.value or ""),
                ).props("dense outlined stack-label").classes("w-32")

                ui.select(
                    options=kind_opts, value=effect.kind, label=t("kind"),
                    on_change=lambda e, i=idx, k=wf_key, sk=step.key:
                        self.set_effect_field(k, sk, i, "kind", e.value or ""),
                ).props("dense outlined stack-label").classes("w-56")

                if effect.kind in ("ad_add_to_groups", "ad_remove_from_groups"):
                    eligible = self._eligible_groups_snapshot
                    groups_select = chip_list_editor(
                        list(effect.params.get("groups", [])),
                        label=t("groups"),
                        suggestions=list(eligible.keys()),
                    )
                    groups_select.on_value_change(
                        lambda e, i=idx, k=wf_key, sk=step.key:
                            self.set_effect_param(k, sk, i, "groups", list(e.value or []))
                    )
                else:
                    ui.label("—").classes("text-grey")

                ui.button(
                    icon="delete",
                    on_click=lambda i=idx, k=wf_key, sk=step.key: self.delete_effect(k, sk, i),
                ).props("flat dense round color=negative")

        ui.button(
            f"+ {t('add_effect')}",
            on_click=lambda k=wf_key, sk=step.key: self.add_effect(k, sk),
        ).props("flat dense color=primary")

    # --- effect mutations ---

    def _on_step_type_change(self, wf_key: str, step_key: str, value: str) -> None:
        self.set_step_field(wf_key, step_key, "type", value)
        if value == "ad_account_creation":
            step = self._find_step(wf_key, step_key)
            if step.actions != ["complete"]:
                step.actions = ["complete"]
        self._refresh_detail()

    def add_effect(self, wf_key: str, step_key: str) -> None:
        step = self._find_step(wf_key, step_key)
        default_action = (step.actions or ["submit"])[0] or "submit"
        step.effects = list(step.effects or []) + [
            StepEffectConfig(on_action=default_action, kind="ad_add_to_groups", params={"groups": []})
        ]
        self._refresh_detail()

    def delete_effect(self, wf_key: str, step_key: str, idx: int) -> None:
        step = self._find_step(wf_key, step_key)
        effs = list(step.effects or [])
        if 0 <= idx < len(effs):
            effs.pop(idx)
            step.effects = effs
        self._refresh_detail()

    def set_effect_field(self, wf_key: str, step_key: str, idx: int, field: str, value) -> None:
        step = self._find_step(wf_key, step_key)
        setattr(step.effects[idx], field, value)
        self._refresh_detail()

    def set_effect_param(self, wf_key: str, step_key: str, idx: int, key: str, value) -> None:
        step = self._find_step(wf_key, step_key)
        params = dict(step.effects[idx].params or {})
        params[key] = value
        step.effects[idx].params = params

    def _render_step_editor(self, wf_key: str, step) -> None:
        from not_dot_net.config import step_display
        from not_dot_net.frontend.workflow_editor_options import action_options

        wf = self.working_copy.workflows[wf_key]
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="arrow_back", on_click=lambda w=wf_key: self.select(w)
                      ).props("flat dense round").tooltip(t("back_to_workflow", name=wf.label or wf_key))
            ui.label(step_display(step)).classes("text-h6")

        ui.input(t("step_label_field"), value=step.label,
                 on_change=lambda e, w=wf_key, k=step.key: self.set_step_field(w, k, "label", e.value)
                 ).classes("w-full").props("dense outlined stack-label")

        ui.select(
            {
                "form": t("step_type_form"),
                "approval": t("step_type_approval"),
                "ad_account_creation": t("step_type_ad_account_creation"),
            },
            value=step.type, label=t("field_type"),
            on_change=lambda e, w=wf_key, k=step.key: self._on_step_type_change(w, k, e.value),
        ).classes("w-full").props("dense outlined stack-label")

        ui.label(t("step_assignee")).classes("text-subtitle2 q-mt-sm")
        opts = assignee_options(self._roles, self._permissions)
        kinds = [
            ("role", t("assignee_kind_role"), [o for o in opts if o["kind"] == "role"]),
            ("permission", t("assignee_kind_permission"), [o for o in opts if o["kind"] == "permission"]),
            ("contextual_requester", t("assignee_kind_requester"), [o for o in opts if o["kind"] == "contextual_requester"]),
            ("contextual_target_person", t("assignee_kind_target"), [o for o in opts if o["kind"] == "contextual_target_person"]),
        ]
        kind_choices = {k: label for k, label, _ in kinds}

        current_val = _current_assignee_value(step)
        if current_val is None:
            current_kind = "role"
        else:
            for k, _, sub in kinds:
                if any(o["value"] == current_val for o in sub):
                    current_kind = k
                    break
            else:
                current_kind = "role"

        kind_select = ui.select(kind_choices, value=current_kind, label=t("step_assignee")
                                ).classes("w-full").props("dense outlined stack-label")

        sub_opts_by_kind = {k: [{"value": o["value"], "label": o["label"]} for o in sub]
                            for k, _, sub in kinds}
        sub_select_container = ui.row().classes("w-full")
        sub_select_holder: dict = {"select": None}

        def _render_sub_select(kind_value: str, *, apply: bool = True) -> None:
            sub_select_container.clear()
            sub_list = sub_opts_by_kind.get(kind_value, [])
            if not sub_list:
                contextual_value = "contextual:requester" if kind_value == "contextual_requester" else "contextual:target_person"
                if apply:
                    self.set_step_assignee_from_picker(wf_key, step.key, contextual_value)
                sub_select_holder["select"] = None
                return
            if len(sub_list) == 1:
                only = sub_list[0]["value"]
                with sub_select_container:
                    ui.label(sub_list[0]["label"]).classes("text-grey")
                if apply:
                    self.set_step_assignee_from_picker(wf_key, step.key, only)
                sub_select_holder["select"] = None
                return
            options_dict = {o["value"]: o["label"] for o in sub_list}
            if current_val and any(o["value"] == current_val for o in sub_list):
                initial = current_val
            else:
                initial = sub_list[0]["value"] if apply else None
            with sub_select_container:
                sub = ui.select(options_dict, value=initial, label=t("step_assignee")
                                ).classes("w-full").props("dense outlined stack-label")
                sub.on_value_change(lambda e, w=wf_key, k=step.key: self.set_step_assignee_from_picker(w, k, e.value))
            sub_select_holder["select"] = sub
            if apply:
                self.set_step_assignee_from_picker(wf_key, step.key, initial)

        _render_sub_select(current_kind, apply=False)
        kind_select.on_value_change(lambda e, _r=_render_sub_select: _r(e.value))

        # actions
        ui.label(t("step_actions")).classes("text-subtitle2 q-mt-sm")
        act_opts = {o["value"]: o["label"] for o in action_options(step.actions)}
        actions_select = ui.select(act_opts, value=list(step.actions or []), multiple=True,
                                   label=t("step_actions")
                                   ).classes("w-full").props("dense outlined stack-label use-chips")

        def _bind_actions(w=actions_select, wk=wf_key, sk=step.key):
            self.set_step_field(wk, sk, "actions", list(w.value or []))
            self._refresh_detail()  # corrections_target visibility may change
        actions_select.on_value_change(lambda e, _b=_bind_actions: _b())

        ui.switch(t("step_partial_save"), value=step.partial_save,
                  on_change=lambda e, w=wf_key, k=step.key: self.set_step_field(w, k, "partial_save", e.value))

        if "request_corrections" in (step.actions or []):
            other_keys = [s.key for s in wf.steps if s.key != step.key]
            ui.select([None, *other_keys], value=step.corrections_target, label=t("step_corrections_target"),
                      on_change=lambda e, w=wf_key, k=step.key: self.set_step_field(w, k, "corrections_target", e.value)
                      ).classes("w-full").props("dense outlined stack-label")

        # --- Effects panel ---
        n_eff = len(step.effects or [])
        with ui.expansion(f"{t('step_section_effects')}  ({n_eff})", icon="bolt").classes("w-full"):
            self._render_effects_table(wf_key, step)

        # --- Fields panel (hidden for ad_account_creation) ---
        ui.label(t("fields")).classes("text-subtitle2 q-mt-md")
        if step.type == "ad_account_creation":
            ui.label(t("ad_account_creation_fields_locked")).classes("text-grey italic")
        else:
            if not step.fields:
                ui.label(t("empty_fields")).classes("text-grey text-sm")

            org_keys = [None, *self._vocab_keys]
            for idx, field in enumerate(step.fields):
                if isinstance(field, FieldRef):
                    self._render_field_ref_row(wf_key, step.key, idx, field, len(step.fields))
                    continue
                with ui.column().classes("w-full"):
                    with ui.row().classes("w-full items-center gap-2 no-wrap"):
                        ui.input(t("field_display_name"), value=field.label,
                                 on_change=lambda e, i=idx, w=wf_key, sk=step.key:
                                     self.set_field_label_with_autoslug(w, sk, i, e.value)
                                 ).props("dense outlined stack-label").classes("grow")
                        ui.select(["text", "email", "phone", "textarea", "date", "select", "file", "location", "checkbox"],
                                  value=field.type, label="type",
                                  on_change=lambda e, i=idx, w=wf_key, sk=step.key:
                                      self.set_field_attr(w, sk, i, "type", e.value)
                                  ).props("dense outlined stack-label").classes("w-32")
                        ui.switch(t("required"), value=field.required,
                                  on_change=lambda e, i=idx, w=wf_key, sk=step.key:
                                      self.set_field_attr(w, sk, i, "required", e.value))
                        ui.switch(t("field_half_width"), value=field.half_width,
                                  on_change=lambda e, i=idx, w=wf_key, sk=step.key:
                                      self.set_field_attr(w, sk, i, "half_width", e.value))
                        with ui.expansion(t("field_more"), icon="more_vert").classes("grow-0"):
                            self._render_field_more(wf_key, step.key, idx, field, org_keys)
                        ui.button(icon="keyboard_arrow_up",
                                  on_click=lambda e, i=idx, w=wf_key, sk=step.key: self.move_field(w, sk, i, -1)
                                  ).props(f"flat dense round size=sm {'disable' if idx == 0 else ''}")
                        ui.button(icon="keyboard_arrow_down",
                                  on_click=lambda e, i=idx, w=wf_key, sk=step.key: self.move_field(w, sk, i, +1)
                                  ).props(f"flat dense round size=sm {'disable' if idx == len(step.fields) - 1 else ''}")
                        ui.button(icon="delete",
                                  on_click=lambda i=idx, w=wf_key, sk=step.key:
                                      self.delete_field(w, sk, i)
                                  ).props("flat dense round color=negative")

            ui.button(t("add_field"),
                      on_click=lambda w=wf_key, sk=step.key: self.add_field(w, sk)
                      ).props("flat dense color=primary")
            def_keys = sorted(self._field_defs)
            if def_keys:
                ui.button(t("field_use_shared"), icon="link",
                          on_click=lambda w=wf_key, sk=step.key, keys=def_keys:
                              self._prompt_use_shared(w, sk, keys)
                          ).props("flat dense color=secondary")

        with ui.expansion(t("step_advanced"), icon="settings").classes("w-full q-mt-md"):
            ui.label(t("step_key_hint")).classes("text-warning text-xs")
            ui.input(t("step_key_field"), value=step.key,
                     on_change=lambda e, w=wf_key, k=step.key: self._safe_set(w, k, "key", e.value)
                     ).classes("w-full").props("dense outlined stack-label")

    def _render_field_more(self, wf_key: str, step_key: str, idx: int, field, org_keys) -> None:
        locked = self._is_field_name_locked(wf_key, step_key, field.name)
        with ui.column().classes("w-full"):
            name_input = ui.input(t("field_internal_name"), value=field.name).props(
                "dense outlined stack-label"
            ).classes("w-full")
            if locked:
                name_input.props("readonly")
                ui.label(t("field_internal_name_warn")).classes("text-warning text-xs")

                def _on_unlock(w=wf_key, sk=step_key, fn=field.name):
                    self.unlock_field_name(w, sk, fn)
                    self._refresh_detail()
                ui.button(t("field_internal_name_unlock"), on_click=_on_unlock
                          ).props("flat dense color=warning")
            else:
                name_input.on_value_change(
                    lambda e, i=idx, w=wf_key, sk=step_key:
                        self._safe_field_rename(w, sk, i, e.value)
                )
            ui.select(org_keys, value=field.options_key, label=t("field_options_key"),
                      on_change=lambda e, i=idx, w=wf_key, sk=step_key:
                          self.set_field_attr(w, sk, i, "options_key", e.value)
                      ).props("dense outlined stack-label").classes("w-full")
            ui.switch(t("field_encrypted"), value=field.encrypted,
                      on_change=lambda e, i=idx, w=wf_key, sk=step_key:
                          self.set_field_attr(w, sk, i, "encrypted", e.value))

            # visible_when picker — same-step checkbox + value, v1
            wf = self.working_copy.workflows[wf_key]
            step = next(s for s in wf.steps if s.key == step_key)
            checkbox_names = [f.name for f in self._resolved_step_fields(step)
                              if f.type == "checkbox" and f.name != field.name]
            current_when = field.visible_when or {}
            current_key = next(iter(current_when), None)
            current_val = current_when.get(current_key)

            ui.label(t("wf_visible_when_help")).classes("text-sm q-mt-sm")
            with ui.row().classes("w-full items-center gap-2"):
                key_select = ui.select(
                    [None, *checkbox_names],
                    value=current_key,
                    label=t("visible_when_checkbox"),
                ).props("dense outlined stack-label").classes("grow")
                ui.label("=").classes("text-grey")
                val_select = ui.select(
                    [True, False],
                    value=current_val if isinstance(current_val, bool) else None,
                    label=t("visible_when_value"),
                ).props("dense outlined stack-label").classes("w-24")

            def _apply(_e=None, w=wf_key, sk=step_key, i=idx,
                       ks=key_select, vs=val_select):
                self._apply_visible_when(w, sk, i, ks.value, vs.value)

            key_select.on_value_change(_apply)
            val_select.on_value_change(_apply)

    def _render_field_ref_row(self, wf_key, step_key, idx, ref, n_fields) -> None:
        defn = self._field_defs.get(ref.ref)
        resolved = resolve_field_ref(ref, defn) if defn else None
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            ui.badge(t("field_shared_badge", ref=ref.ref)).props("color=secondary")
            if resolved is not None:
                ui.label(resolved.label or resolved.name).classes("grow")
                ui.label(resolved.type).classes("text-grey text-xs")
            else:
                ui.label(t("field_ref_dangling", ref=ref.ref)).classes("text-negative grow")
            ui.button(t("field_edit_overrides"), icon="tune",
                      on_click=lambda w=wf_key, sk=step_key, i=idx, r=ref:
                          self._open_override_dialog(w, sk, i, r)
                      ).props("flat dense")
            ui.button(icon="keyboard_arrow_up",
                      on_click=lambda w=wf_key, sk=step_key, i=idx: self.move_field(w, sk, i, -1)
                      ).props(f"flat dense round size=sm {'disable' if idx == 0 else ''}")
            ui.button(icon="keyboard_arrow_down",
                      on_click=lambda w=wf_key, sk=step_key, i=idx: self.move_field(w, sk, i, +1)
                      ).props(f"flat dense round size=sm {'disable' if idx == n_fields - 1 else ''}")
            ui.button(icon="delete",
                      on_click=lambda w=wf_key, sk=step_key, i=idx: self.delete_field(w, sk, i)
                      ).props("flat dense round color=negative")

    def _prompt_use_shared(self, wf_key, step_key, def_keys) -> None:
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label(t("field_use_shared"))
            sel = ui.select(def_keys, value=def_keys[0], label=t("field_definitions")
                            ).props("outlined dense stack-label").classes("w-64")

            def add():
                self.add_field_ref(wf_key, step_key, sel.value)
                dlg.close()

            ui.button(t("save"), on_click=add).props("color=primary")
        dlg.open()

    def _open_override_dialog(self, wf_key, step_key, idx, ref) -> None:
        from not_dot_net.backend.field_definitions import FieldDefinition as _FD
        defn = self._field_defs.get(ref.ref)
        dlg = ui.dialog()
        with dlg, ui.card().classes("w-full"):
            ui.label(t("field_shared_badge", ref=ref.ref)).classes("text-h6")

            def row(attr, widget_factory, base_value):
                with ui.row().classes("w-full items-center gap-2"):
                    overridden = getattr(ref, attr) is not None
                    sw = ui.switch(f"{t('field_override_toggle')}: {attr}", value=overridden)
                    w = widget_factory(getattr(ref, attr) if overridden else base_value)
                    w.set_enabled(overridden)

                    def on_toggle(e, _attr=attr, _w=w):
                        _w.set_enabled(e.value)
                        self.set_field_ref_override(
                            wf_key, step_key, idx, _attr, _w.value if e.value else None)
                    sw.on_value_change(on_toggle)
                return w

            base = defn or _FD(key=ref.ref, type="text")

            label_w = row("label", lambda v: ui.input(t("field_display_name"), value=v or "")
                          .props("outlined dense stack-label"), base.label)
            label_w.on_value_change(lambda e: self.set_field_ref_override(wf_key, step_key, idx, "label", e.value))

            req_w = row("required", lambda v: ui.switch(t("field_required"), value=bool(v)), base.required)
            req_w.on_value_change(lambda e: self.set_field_ref_override(wf_key, step_key, idx, "required", e.value))

            hw_w = row("half_width", lambda v: ui.switch(t("field_half_width"), value=bool(v)), base.half_width)
            hw_w.on_value_change(lambda e: self.set_field_ref_override(wf_key, step_key, idx, "half_width", e.value))

            ui.button(t("save"), on_click=lambda: (dlg.close(), self._refresh_detail())
                      ).props("color=primary")
        dlg.open()

    def _apply_visible_when(self, wf_key: str, step_key: str, idx: int,
                            key: str | None, value) -> None:
        """Update a field's `visible_when` from picker state.

        Called on every value change of either picker; partial state
        (key without value, or vice-versa) clears the rule but must not
        rebuild the detail pane — that would destroy the picker mid-edit.
        """
        rule = {key: value} if key and value is not None else None
        self.set_field_attr(wf_key, step_key, idx, "visible_when", rule)

    def _safe_field_rename(self, wf_key: str, step_key: str, idx: int, new_name: str) -> None:
        try:
            self.set_field_attr(wf_key, step_key, idx, "name", new_name)
        except ValueError as e:
            notify_error(e)

    def _safe_set(self, wf_key: str, step_key: str, field: str, value) -> None:
        try:
            self.set_step_field(wf_key, step_key, field, value)
        except ValueError as e:
            notify_error(e)
        except KeyError:
            pass  # stale closure from pre-rename detail pane — silently ignore

    def dump_yaml(self) -> str:
        self._collect_widget_state()
        return safe_dump(self.working_copy.model_dump(), default_flow_style=False, allow_unicode=True)

    def apply_yaml(self, yaml_str: str) -> None:
        try:
            data = safe_load(yaml_str)
            new_cfg = WorkflowsConfig.model_validate(data)
        except Exception as e:
            raise ValueError(str(e)) from e
        self.working_copy = new_cfg
        self.selected_workflow = next(iter(self.working_copy.workflows), None)
        self.selected_step = None
        self._refresh_tree()
        self._refresh_detail()

    def _collect_widget_state(self) -> None:
        wf_doc = self._workflow_doc_instructions_widget
        if wf_doc:
            wf_key, widget = wf_doc
            if wf_key in self.working_copy.workflows:
                self.working_copy.workflows[wf_key].document_instructions = widget.value

    def _on_add_workflow_click(self) -> None:
        def _create(name: str) -> None:
            key = display_name_to_key(name, set(self.working_copy.workflows), fallback_prefix="workflow")
            self.add_workflow(key, label=name)
        self._prompt_for_name(t("new_workflow_prompt"), _create)

    def _on_duplicate_click(self, src_key: str) -> None:
        def _create(name: str, src=src_key) -> None:
            key = display_name_to_key(name, set(self.working_copy.workflows), fallback_prefix="workflow")
            self.duplicate_workflow(src, key, label=name)
        self._prompt_for_name(t("duplicate_workflow_prompt"), _create)

    def _on_add_step_click(self, wf_key: str) -> None:
        def _create(name: str, wk=wf_key) -> None:
            taken = {s.key for s in self.working_copy.workflows[wk].steps}
            key = display_name_to_key(name, taken, fallback_prefix="step")
            self.add_step(wk, key, label=name)
        self._prompt_for_name(t("new_step_prompt"), _create)

    def _prompt_for_name(self, prompt: str, callback) -> None:
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label(prompt)
            inp = ui.input(label=t("name_label")).props("dense outlined stack-label autofocus")
            err = ui.label("").classes("text-negative text-sm")

            def confirm():
                value = (inp.value or "").strip()
                if not value:
                    err.set_text(t("name_required"))
                    return
                try:
                    callback(value)
                    dlg.close()
                except ValueError as e:
                    err.set_text(str(e))

            inp.on("keydown.enter", lambda e: confirm())
            with ui.row():
                ui.button(t("ok"), on_click=confirm).props("color=primary")
                ui.button(t("cancel"), on_click=dlg.close).props("flat")
        dlg.open()

    # --- validation & dirty tracking ---

    def is_dirty(self) -> bool:
        self._collect_widget_state()
        return self.working_copy.model_dump() != self.original.model_dump()

    def _update_save_dirty_indicator(self) -> None:
        if self._save_dirty_badge is None:
            return
        self._save_dirty_badge.visible = self.is_dirty()

    def _resolved_step_fields(self, step) -> list:
        """Resolve a step's fields against the definitions snapshot.
        Drops dangling references (compute_warnings reports them separately)."""
        out = []
        for item in step.fields:
            if isinstance(item, FieldRef):
                defn = self._field_defs.get(item.ref)
                if defn is not None:
                    out.append(resolve_field_ref(item, defn))
            else:
                out.append(item)
        return out

    def compute_warnings(self) -> list[str]:
        warnings: list[str] = []
        org_list_keys = set(self._vocab_keys)
        for wf_key, wf in self.working_copy.workflows.items():
            if not wf.steps:
                warnings.append(f"[{wf_key}] workflow has no steps — it will be hidden from the new-request page")
            seen_step_keys: set[str] = set()
            step_keys: list[str] = []
            for step in wf.steps:
                if step.key in seen_step_keys:
                    warnings.append(f"[{wf_key}] duplicate step key '{step.key}'")
                seen_step_keys.add(step.key)
                step_keys.append(step.key)
            field_names = {f.name for s in wf.steps for f in self._resolved_step_fields(s)}
            if wf.target_email_field and wf.target_email_field not in field_names:
                warnings.append(
                    f"[{wf_key}] target_email_field '{wf.target_email_field}' does not match any field name"
                )
            if wf.tenure is not None:
                # The hook reads request data by these names; a dangling one
                # means no tenure gets recorded, with nothing to show for it.
                # returning_user_field is deliberately not checked: the
                # new-request picker injects it, it is never a declared field.
                for attr, declared in (
                    ("status_field", wf.tenure.status_field),
                    ("employer_field", wf.tenure.employer_field),
                    ("start_date_field", wf.tenure.start_date_field),
                ):
                    if declared and declared not in field_names:
                        warnings.append(
                            f"[{wf_key}] tenure {attr} '{declared}' does not match any field name"
                        )
            for step in wf.steps:
                if "request_corrections" in (step.actions or []):
                    if step.corrections_target and step.corrections_target not in step_keys:
                        warnings.append(
                            f"[{wf_key}/{step.key}] corrections_target '{step.corrections_target}' does not exist"
                        )
                for item in step.fields:
                    if isinstance(item, FieldRef) and item.ref not in self._field_defs:
                        warnings.append(
                            f"[{wf_key}/{step.key}] ref '{item.ref}' is not a known field definition"
                        )
                resolved = self._resolved_step_fields(step)
                for f in resolved:
                    if f.options_key and f.options_key not in org_list_keys:
                        warnings.append(
                            f"[{wf_key}/{step.key}/{f.name}] options_key '{f.options_key}' is not a known vocabulary"
                        )
                    if f.encrypted and f.type != "file":
                        warnings.append(
                            f"[{wf_key}/{step.key}/{f.name}] encrypted only applies to file fields — "
                            f"a '{f.type}' value is stored in cleartext"
                        )
                checkbox_names = {f.name for f in resolved if f.type == "checkbox"}
                for f in resolved:
                    if not f.visible_when:
                        continue
                    for k in f.visible_when:
                        if k not in checkbox_names:
                            warnings.append(
                                f"[{wf_key}/{step.key}/{f.name}] visible_when references "
                                f"'{k}' which is not a checkbox in the same step"
                            )
            known_recip_values = {o["value"] for o in recipient_options(self._roles, self._permissions)}
            for nr in wf.notifications:
                if nr.step and nr.step not in step_keys:
                    warnings.append(f"[{wf_key}] notification rule references missing step '{nr.step}'")
                for v in (nr.notify or []):
                    if v not in known_recip_values:
                        warnings.append(
                            f"[{wf_key}] notification recipient '{v}' not found in current roles or permissions"
                        )
            di_seen: set[str] = set()
            for k in wf.document_instructions:
                if k in di_seen:
                    warnings.append(
                        f"[{wf_key}] duplicate document_instructions key '{k}' — one entry will be lost on save"
                    )
                di_seen.add(k)
            for step in wf.steps:
                valid_actions = set(step.actions or [])
                for eff in (step.effects or []):
                    if eff.on_action not in valid_actions:
                        warnings.append(
                            f"[{wf_key}/{step.key}] effect references unknown action '{eff.on_action}'"
                        )
                    if eff.kind in ("ad_add_to_groups", "ad_remove_from_groups"):
                        groups = eff.params.get("groups") or []
                        bad = [g for g in groups if g not in self._eligible_groups_snapshot]
                        if bad:
                            warnings.append(
                                f"[{wf_key}/{step.key}] effect references groups not in eligible_groups: {', '.join(bad)}"
                            )
        return warnings

    def warnings_for(self, wf_key: str, step_key: str | None = None) -> list[str]:
        """Scope the flat warning strings to one workflow or one step, relying on
        the existing '[wf]' / '[wf/step]' / '[wf/step/field]' prefix convention."""
        if step_key is None:
            prefixes = (f"[{wf_key}]", f"[{wf_key}/")
        else:
            prefixes = (f"[{wf_key}/{step_key}]", f"[{wf_key}/{step_key}/")
        return [w for w in self._current_warnings if w.startswith(prefixes)]

    def _show_warnings(self, warnings: list[str]) -> None:
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label(t("configuration_warnings")).classes("text-h6")
            for w in warnings:
                ui.label(f"• {w}")
            ui.button(t("close"), on_click=dlg.close).props("flat")
        dlg.open()

    def _confirm(self, message: str, on_confirm) -> None:
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label(message)
            with ui.row():
                ui.button(t("delete"), on_click=lambda: (dlg.close(), on_confirm())).props("color=negative")
                ui.button(t("cancel"), on_click=dlg.close).props("flat")
        dlg.open()

    def _confirm_delete_workflow(self, key: str) -> None:
        wf = self.working_copy.workflows.get(key)
        name = (wf.label if wf else None) or key
        self._confirm(t("confirm_delete", name=name), lambda k=key: self.delete_workflow(k))

    def _confirm_delete_step(self, wf_key: str, step_key: str) -> None:
        from not_dot_net.config import step_display
        try:
            name = step_display(self._find_step(wf_key, step_key))
        except KeyError:
            name = step_key
        self._confirm(t("confirm_delete", name=name),
                      lambda w=wf_key, s=step_key: self.delete_step(w, s))

    def _on_cancel_click(self) -> None:
        if not self.is_dirty():
            self.close()
            return
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label(t("confirm_discard_changes"))
            with ui.row():
                ui.button(t("discard"), on_click=lambda: (dlg.close(), self.close())).props("color=negative")
                ui.button(t("keep_editing"), on_click=dlg.close).props("flat")
        dlg.open()

    # --- lifecycle ---

    def open(self) -> None:
        if self.dialog:
            self.dialog.open()

    def close(self) -> None:
        if self.dialog:
            self.dialog.close()

    async def save(self) -> None:
        if self._active_tab == "YAML" and self._yaml_editor is not None:
            try:
                self.apply_yaml(self._yaml_editor.value)
            except ValueError as err:
                ui.notify(t("invalid_yaml", error=err), color="negative", multi_line=True)
                return
        else:
            self._collect_widget_state()
        # Re-check at the sink, as admin_settings and pages do. The dialog may
        # have been open across a role change, and this is the most powerful
        # config write in the app.
        from not_dot_net.backend.permissions import check_permission
        try:
            await check_permission(self.user, "manage_settings")
        except PermissionError:
            ui.notify(t("permission_denied"), color="negative")
            return

        try:
            validated = WorkflowsConfig.model_validate(self.working_copy.model_dump())
        except ValidationError as e:
            notify_error(e)
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
        self.selected_workflow = next(iter(self.working_copy.workflows), None)
        self.selected_step = None
        self._refresh_tree()
        self._refresh_detail()
        await log_audit(
            "settings", "reset",
            actor_id=self.user.id, actor_email=self.user.email,
            detail="section=workflows",
        )
        ui.notify(t("settings_reset"), color="info")


async def open_workflow_editor(user) -> None:
    dlg = await WorkflowEditorDialog.create(user)
    dlg.open()
