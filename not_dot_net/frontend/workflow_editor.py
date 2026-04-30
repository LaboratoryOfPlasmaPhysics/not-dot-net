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
from not_dot_net.backend.workflow_service import workflows_config, WorkflowsConfig
from not_dot_net.config import FieldConfig, NotificationRuleConfig, OrgConfig, WorkflowConfig, WorkflowStepConfig
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.workflow_editor_options import (
    assignee_options,
    event_options,
    recipient_options,
    _slugify,
)

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _org_list_field_names() -> list[str]:
    return [
        name for name, info in OrgConfig.model_fields.items()
        if info.annotation == list[str]
    ]


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
        self._warnings_label = None
        self._current_warnings: list[str] = []
        self._roles: dict = {}
        self._permissions: dict = {}
        self._unlocked_fields: set[tuple[str, str, str]] = set()

    @classmethod
    async def create(cls, user) -> "WorkflowEditorDialog":
        original = await workflows_config.get()
        instance = cls(user, original)
        roles_cfg = await roles_config.get()
        instance._roles = dict(roles_cfg.roles)
        instance._permissions = dict(get_permissions())
        instance._build()
        return instance

    def _build(self) -> None:
        self.dialog = ui.dialog().props("maximized")
        with self.dialog, ui.card().classes("w-full h-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(t("workflows_editor")).classes("text-h6")
            with ui.tabs() as tabs:
                ui.tab("Form")
                ui.tab("YAML")
            with ui.tab_panels(tabs, value="Form").classes("w-full grow"):
                with ui.tab_panel("Form"):
                    with ui.row().classes("w-full grow no-wrap"):
                        self._tree_container = ui.column().classes("w-72 q-pr-md").style("border-right: 1px solid #e0e0e0")
                        self._detail_container = ui.column().classes("grow")
                with ui.tab_panel("YAML"):
                    self._yaml_editor = ui.codemirror(self.dump_yaml(), language="yaml").classes("w-full").style("min-height: 400px")
            tabs.on_value_change(self._on_tab_change)
            with ui.row().classes("w-full justify-between items-center"):
                self._warnings_label = ui.label("").classes("text-warning text-sm")
                self._warnings_label.on("click", lambda e: self._show_warnings(self._current_warnings))
                with ui.row():
                    ui.button(t("cancel"), on_click=self._on_cancel_click).props("flat")
                    ui.button(t("reset_defaults"), on_click=self.reset).props("flat color=grey")
                    ui.button(t("save"), on_click=self.save).props("color=primary")
        self._refresh_tree()
        self._refresh_detail()

    # --- workflow mutations ---

    def add_workflow(self, key: str) -> None:
        _validate_slug(key)
        if key in self.working_copy.workflows:
            raise ValueError(f"Workflow '{key}' already exists")
        self.working_copy.workflows[key] = WorkflowConfig(label=key, steps=[])
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

    def duplicate_workflow(self, src_key: str, new_key: str) -> None:
        _validate_slug(new_key)
        if new_key in self.working_copy.workflows:
            raise ValueError(f"Workflow '{new_key}' already exists")
        if src_key not in self.working_copy.workflows:
            raise ValueError(f"Workflow '{src_key}' does not exist")
        self.working_copy.workflows[new_key] = self.working_copy.workflows[src_key].model_copy(deep=True)
        self.selected_workflow = new_key
        self.selected_step = None
        self._refresh_tree()
        self._refresh_detail()

    # --- step mutations ---

    def add_step(self, wf_key: str, step_key: str) -> None:
        _validate_slug(step_key)
        wf = self.working_copy.workflows[wf_key]
        if any(s.key == step_key for s in wf.steps):
            raise ValueError(f"Step '{step_key}' already exists in workflow '{wf_key}'")
        wf.steps.append(WorkflowStepConfig(key=step_key, type="form"))
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
                if f.name == field_name:
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
        taken = {f.name for j, f in enumerate(step.fields) if j != index and f.name}
        field.name = _slugify(label, taken)

    def delete_field(self, wf_key: str, step_key: str, index: int) -> None:
        step = self._find_step(wf_key, step_key)
        del step.fields[index]
        self._refresh_detail()

    # --- rendering ---

    def _refresh_tree(self) -> None:
        if self._tree_container is None:
            return
        self._tree_container.clear()
        with self._tree_container:
            for wf_key, wf in self.working_copy.workflows.items():
                self._render_workflow_header(wf_key, wf)
                for step in wf.steps:
                    self._render_step_row(wf_key, step.key)
            ui.button("+ Add workflow", on_click=self._on_add_workflow_click).props("flat dense color=primary")

    def _render_workflow_header(self, wf_key: str, wf) -> None:
        is_selected = self.selected_workflow == wf_key and self.selected_step is None
        with ui.row().classes(f"w-full items-center {'bg-blue-1' if is_selected else ''}"):
            ui.button(wf.label or wf_key, on_click=lambda k=wf_key: self.select(k)).props("flat dense").classes("grow text-left")
            ui.button(icon="content_copy", on_click=lambda k=wf_key: self._on_duplicate_click(k)).props("flat dense round size=sm")
            ui.button(icon="delete", on_click=lambda k=wf_key: self.delete_workflow(k)).props("flat dense round size=sm color=negative")

    def _render_step_row(self, wf_key: str, step_key: str) -> None:
        is_selected = self.selected_workflow == wf_key and self.selected_step == step_key
        with ui.row().classes(f"w-full items-center q-pl-md {'bg-blue-1' if is_selected else ''}"):
            ui.button(f"• {step_key}", on_click=lambda w=wf_key, s=step_key: self.select(w, s)).props("flat dense").classes("grow text-left")
            ui.button(icon="delete", on_click=lambda w=wf_key, s=step_key: self.delete_step(w, s)).props("flat dense round size=sm color=negative")

    def _refresh_detail(self) -> None:
        if self._detail_container is None:
            return
        self._collect_widget_state()
        self._workflow_doc_instructions_widget = None
        self._detail_container.clear()
        with self._detail_container:
            if self.selected_workflow is None:
                ui.label("No workflow selected. Add one to begin.").classes("text-grey")
                return
            wf = self.working_copy.workflows[self.selected_workflow]
            if self.selected_step is None:
                self._render_workflow_editor(self.selected_workflow, wf)
            else:
                step = self._find_step(self.selected_workflow, self.selected_step)
                self._render_step_editor(self.selected_workflow, step)
            ui.button(
                f"+ Add step to {self.selected_workflow}",
                on_click=lambda k=self.selected_workflow: self._on_add_step_click(k),
            ).props("flat dense color=primary")
        if self._warnings_label is not None:
            self._current_warnings = self.compute_warnings()
            if self._current_warnings:
                self._warnings_label.set_text(f"⚠ {len(self._current_warnings)} issue(s) — click to view")
                self._warnings_label.classes(replace="text-warning text-sm cursor-pointer")
            else:
                self._warnings_label.set_text("")
                self._warnings_label.classes(replace="text-warning text-sm")

    def _render_workflow_editor(self, wf_key: str, wf) -> None:
        from not_dot_net.frontend.widgets import keyed_chip_editor

        ui.label(f"Workflow: {wf_key}").classes("text-h6")

        # --- Section 1: Basics (open by default) ---
        with ui.expansion(t("wf_section_basics"), value=True, icon="info").classes("w-full"):
            ui.input(t("wf_label"), value=wf.label,
                     on_change=lambda e, k=wf_key: self.set_workflow_label(k, e.value)
                     ).classes("w-full").props("dense outlined stack-label").tooltip(t("wf_label_help"))

            ui.input(t("wf_start_role"), value=wf.start_role or "",
                     on_change=lambda e, k=wf_key: self.set_workflow_field(k, "start_role", e.value)
                     ).classes("w-full").props("dense outlined stack-label").tooltip(t("wf_start_role_help"))

            with ui.expansion(t("wf_section_about_other"), icon="person_search").classes("w-full"):
                field_names = sorted({f.name for s in wf.steps for f in s.fields if f.name})
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

        # --- Section 2: Notifications (collapsed; show count) ---
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
                    label="event",
                    on_change=lambda e, i=idx, k=wf_key: self.set_notification_event(k, i, e.value or ""),
                ).props("dense outlined stack-label").classes("w-44")

                step_choices = {None: t("any_step"), **{sk: sk for sk in step_keys}}
                ui.select(
                    options=step_choices,
                    value=rule.step,
                    label="step",
                    on_change=lambda e, i=idx, k=wf_key: self.set_notification_step(k, i, e.value),
                ).props("dense outlined stack-label").classes("w-40")

                # Show "Unknown: <raw>" for any persisted value not in current options.
                missing = [v for v in (rule.notify or []) if v not in recip_value_to_label]
                effective_options = {o["value"]: o["label"] for o in recip_opts}
                for v in missing:
                    effective_options[v] = f"Unknown: {v}"

                recip_select = ui.select(
                    options=effective_options,
                    value=list(rule.notify or []),
                    multiple=True,
                    label="recipients",
                ).props("dense outlined stack-label use-chips").classes("grow")

                def _bind_recip(w=recip_select, i=idx, k=wf_key):
                    self.set_notification_recipients(k, i, list(w.value or []))
                recip_select.on_value_change(lambda e, _b=_bind_recip: _b())

                ui.button(icon="delete",
                          on_click=lambda i=idx, k=wf_key: self.delete_notification_rule(k, i)
                          ).props("flat dense round color=negative")

        ui.button("+ Add notification rule",
                  on_click=lambda k=wf_key: self.add_notification_rule(k)
                  ).props("flat dense color=primary")

    def _render_step_editor(self, wf_key: str, step) -> None:
        from not_dot_net.frontend.widgets import chip_list_editor

        ui.label(f"Step: {step.key}").classes("text-h6")

        ui.input("key", value=step.key,
                 on_change=lambda e, w=wf_key, k=step.key: self._safe_set(w, k, "key", e.value)
                 ).classes("w-full").props("dense outlined stack-label")

        ui.select(["form", "approval"], value=step.type, label="type",
                  on_change=lambda e, w=wf_key, k=step.key: self.set_step_field(w, k, "type", e.value)
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

        def _render_sub_select(kind_value: str) -> None:
            sub_select_container.clear()
            sub_list = sub_opts_by_kind.get(kind_value, [])
            if not sub_list:
                contextual_value = "contextual:requester" if kind_value == "contextual_requester" else "contextual:target_person"
                self.set_step_assignee_from_picker(wf_key, step.key, contextual_value)
                sub_select_holder["select"] = None
                return
            if len(sub_list) == 1:
                only = sub_list[0]["value"]
                with sub_select_container:
                    ui.label(sub_list[0]["label"]).classes("text-grey")
                self.set_step_assignee_from_picker(wf_key, step.key, only)
                sub_select_holder["select"] = None
                return
            options_dict = {o["value"]: o["label"] for o in sub_list}
            initial = current_val if (current_val and any(o["value"] == current_val for o in sub_list)) else sub_list[0]["value"]
            with sub_select_container:
                sub = ui.select(options_dict, value=initial, label=t("step_assignee")
                                ).classes("w-full").props("dense outlined stack-label")
                sub.on_value_change(lambda e, w=wf_key, k=step.key: self.set_step_assignee_from_picker(w, k, e.value))
            sub_select_holder["select"] = sub
            self.set_step_assignee_from_picker(wf_key, step.key, initial)

        _render_sub_select(current_kind)
        kind_select.on_value_change(lambda e, _r=_render_sub_select: _r(e.value))

        # actions
        actions_widget = chip_list_editor(step.actions,
                                          suggestions=["submit", "approve", "reject", "request_corrections", "cancel"])

        def _bind_actions(w=actions_widget, wk=wf_key, sk=step.key):
            self.set_step_field(wk, sk, "actions", list(w.value))
            self._refresh_detail()  # corrections_target visibility may change
        actions_widget.on_value_change(lambda e, _b=_bind_actions: _b())

        ui.switch("partial_save", value=step.partial_save,
                  on_change=lambda e, w=wf_key, k=step.key: self.set_step_field(w, k, "partial_save", e.value))

        if "request_corrections" in (step.actions or []):
            wf = self.working_copy.workflows[wf_key]
            other_keys = [s.key for s in wf.steps if s.key != step.key]
            ui.select([None, *other_keys], value=step.corrections_target, label="corrections_target",
                      on_change=lambda e, w=wf_key, k=step.key: self.set_step_field(w, k, "corrections_target", e.value)
                      ).classes("w-full").props("dense outlined stack-label")

        ui.label("Fields").classes("text-subtitle2 q-mt-md")
        if not step.fields:
            ui.label(t("empty_fields")).classes("text-grey text-sm")

        org_keys = [None, *_org_list_field_names()]
        for idx, field in enumerate(step.fields):
            with ui.column().classes("w-full"):
                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    ui.input(t("field_display_name"), value=field.label,
                             on_change=lambda e, i=idx, w=wf_key, sk=step.key:
                                 self.set_field_label_with_autoslug(w, sk, i, e.value)
                             ).props("dense outlined stack-label").classes("grow")
                    ui.select(["text", "email", "textarea", "date", "select", "file"],
                              value=field.type, label="type",
                              on_change=lambda e, i=idx, w=wf_key, sk=step.key:
                                  self.set_field_attr(w, sk, i, "type", e.value)
                              ).props("dense outlined stack-label").classes("w-32")
                    ui.switch("Required", value=field.required,
                              on_change=lambda e, i=idx, w=wf_key, sk=step.key:
                                  self.set_field_attr(w, sk, i, "required", e.value))
                    ui.switch(t("field_half_width"), value=field.half_width,
                              on_change=lambda e, i=idx, w=wf_key, sk=step.key:
                                  self.set_field_attr(w, sk, i, "half_width", e.value))
                    with ui.expansion(t("field_more"), icon="more_vert").classes("grow-0"):
                        self._render_field_more(wf_key, step.key, idx, field, org_keys)
                    ui.button(icon="delete",
                              on_click=lambda i=idx, w=wf_key, sk=step.key:
                                  self.delete_field(w, sk, i)
                              ).props("flat dense round color=negative")

        ui.button("+ Add field",
                  on_click=lambda w=wf_key, sk=step.key: self.add_field(w, sk)
                  ).props("flat dense color=primary")

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

    def _safe_field_rename(self, wf_key: str, step_key: str, idx: int, new_name: str) -> None:
        try:
            self.set_field_attr(wf_key, step_key, idx, "name", new_name)
        except ValueError as e:
            ui.notify(str(e), color="negative")

    def _safe_set(self, wf_key: str, step_key: str, field: str, value) -> None:
        try:
            self.set_step_field(wf_key, step_key, field, value)
        except ValueError as e:
            ui.notify(str(e), color="negative")
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

    def _on_tab_change(self, e) -> None:
        new_tab = e.value
        if new_tab == "YAML":
            self._collect_widget_state()
            if self._yaml_editor is not None:
                self._yaml_editor.value = self.dump_yaml()
        elif new_tab == "Form":
            if self._yaml_editor is not None:
                try:
                    self.apply_yaml(self._yaml_editor.value)
                except ValueError as err:
                    ui.notify(f"Invalid YAML: {err}", color="negative", multi_line=True)
                    return  # stay on YAML
        self._active_tab = new_tab

    def _collect_widget_state(self) -> None:
        wf_doc = self._workflow_doc_instructions_widget
        if wf_doc:
            wf_key, widget = wf_doc
            if wf_key in self.working_copy.workflows:
                self.working_copy.workflows[wf_key].document_instructions = widget.value

    def _on_add_workflow_click(self) -> None:
        self._prompt_for_key("New workflow key", lambda k: self.add_workflow(k))

    def _on_duplicate_click(self, src_key: str) -> None:
        self._prompt_for_key(f"Duplicate '{src_key}' as", lambda k: self.duplicate_workflow(src_key, k))

    def _on_add_step_click(self, wf_key: str) -> None:
        self._prompt_for_key("New step key", lambda k: self.add_step(wf_key, k))

    def _prompt_for_key(self, prompt: str, callback) -> None:
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label(prompt)
            inp = ui.input(label="key").props("dense outlined stack-label autofocus")
            err = ui.label("").classes("text-negative text-sm")

            def confirm():
                try:
                    callback(inp.value.strip())
                    dlg.close()
                except ValueError as e:
                    err.set_text(str(e))

            with ui.row():
                ui.button("OK", on_click=confirm).props("color=primary")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    # --- validation & dirty tracking ---

    def is_dirty(self) -> bool:
        self._collect_widget_state()
        return self.working_copy.model_dump() != self.original.model_dump()

    def compute_warnings(self) -> list[str]:
        warnings: list[str] = []
        org_list_keys = set(_org_list_field_names())
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
            field_names = {f.name for s in wf.steps for f in s.fields}
            if wf.target_email_field and wf.target_email_field not in field_names:
                warnings.append(
                    f"[{wf_key}] target_email_field '{wf.target_email_field}' does not match any field name"
                )
            for step in wf.steps:
                if "request_corrections" in (step.actions or []):
                    if step.corrections_target and step.corrections_target not in step_keys:
                        warnings.append(
                            f"[{wf_key}/{step.key}] corrections_target '{step.corrections_target}' does not exist"
                        )
                for f in step.fields:
                    if f.options_key and f.options_key not in org_list_keys:
                        warnings.append(
                            f"[{wf_key}/{step.key}/{f.name}] options_key '{f.options_key}' is not an OrgConfig list field"
                        )
            for nr in wf.notifications:
                if nr.step and nr.step not in step_keys:
                    warnings.append(f"[{wf_key}] notification rule references missing step '{nr.step}'")
            di_seen: set[str] = set()
            for k in wf.document_instructions:
                if k in di_seen:
                    warnings.append(
                        f"[{wf_key}] duplicate document_instructions key '{k}' — one entry will be lost on save"
                    )
                di_seen.add(k)
        return warnings

    def _show_warnings(self, warnings: list[str]) -> None:
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label("Configuration warnings").classes("text-h6")
            for w in warnings:
                ui.label(f"• {w}")
            ui.button("Close", on_click=dlg.close).props("flat")
        dlg.open()

    def _on_cancel_click(self) -> None:
        if not self.is_dirty():
            self.close()
            return
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label("Discard unsaved changes?")
            with ui.row():
                ui.button("Discard", on_click=lambda: (dlg.close(), self.close())).props("color=negative")
                ui.button("Keep editing", on_click=dlg.close).props("flat")
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
                ui.notify(f"Invalid YAML: {err}", color="negative", multi_line=True)
                return
        else:
            self._collect_widget_state()
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
