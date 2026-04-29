# Workflow Form Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the YAML editor for the `workflows` config section with a master-detail form-based dialog, and upgrade `list[str]` / `dict[str, list[str]]` editors elsewhere with chip widgets.

**Architecture:** Two new modules — `frontend/widgets.py` (reusable chip widgets) and `frontend/workflow_editor.py` (the dialog). `frontend/admin_settings.py` is modified to dispatch to the new widgets and to swap the YAML editor for an "Edit workflows…" button when the section is `workflows`. The dialog edits a Pydantic working copy of `WorkflowsConfig` held in dialog-local state and persists via the existing `workflows_config.set()`.

**Tech Stack:** NiceGUI (Quasar `q-select` `use-chips` mode for the chip widget; `ui.dialog().props("maximized")`; `ui.codemirror`), Pydantic v2, existing `ConfigSection[T]` registry.

**Spec:** `docs/superpowers/specs/2026-04-29-workflow-form-editor-design.md`

---

### Task 1: `chip_list_editor` widget

**Files:**
- Create: `not_dot_net/frontend/widgets.py`
- Test: `tests/test_widgets.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_widgets.py
"""Tests for reusable settings widgets."""

import pytest
from nicegui import ui
from nicegui.testing import User

from not_dot_net.frontend.widgets import chip_list_editor


async def test_chip_list_editor_initial_value(user: User):
    @ui.page("/_w1")
    def _page():
        chip_list_editor(["a", "b", "c"]).props("data-testid=chips")
    await user.open("/_w1")
    select = user.find("data-testid=chips").elements.pop()
    assert list(select.value) == ["a", "b", "c"]


async def test_chip_list_editor_returns_list_type(user: User):
    @ui.page("/_w2")
    def _page():
        w = chip_list_editor([])
        assert isinstance(w.value, list)
    await user.open("/_w2")


async def test_chip_list_editor_writes_back_list(user: User):
    captured = {}

    @ui.page("/_w3")
    def _page():
        w = chip_list_editor(["x"])
        captured["w"] = w
    await user.open("/_w3")
    captured["w"].value = ["x", "y"]
    assert captured["w"].value == ["x", "y"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_widgets.py -v
```
Expected: FAIL — `ModuleNotFoundError: not_dot_net.frontend.widgets`

- [ ] **Step 3: Implement `chip_list_editor`**

```python
# not_dot_net/frontend/widgets.py
"""Reusable input widgets used in admin settings forms."""

from nicegui import ui


def chip_list_editor(
    value: list[str],
    *,
    label: str = "",
    suggestions: list[str] | None = None,
):
    """Chip-style multi-value text input.

    Backed by a Quasar q-select in `use-chips` + `use-input` mode with
    `new-value-mode="add-unique"`. Reads/writes a `list[str]`.
    """
    options = list(suggestions) if suggestions else []
    select = ui.select(
        options=options,
        value=list(value),
        label=label or None,
        multiple=True,
        new_value_mode="add-unique",
    ).props('use-chips use-input outlined dense hide-dropdown-icon input-debounce=0').classes("w-full")
    return select
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_widgets.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/widgets.py tests/test_widgets.py
git commit -m "feat(widgets): add chip_list_editor for list[str] settings fields"
```

---

### Task 2: `keyed_chip_editor` widget

**Files:**
- Modify: `not_dot_net/frontend/widgets.py`
- Modify: `tests/test_widgets.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_widgets.py`:

```python
from not_dot_net.frontend.widgets import keyed_chip_editor


async def test_keyed_chip_editor_initial_value(user: User):
    captured = {}

    @ui.page("/_k1")
    def _page():
        captured["w"] = keyed_chip_editor({"Linux": ["bash"], "Windows": ["powershell"]})
    await user.open("/_k1")
    assert captured["w"].value == {"Linux": ["bash"], "Windows": ["powershell"]}


async def test_keyed_chip_editor_add_remove_key(user: User):
    captured = {}

    @ui.page("/_k2")
    def _page():
        captured["w"] = keyed_chip_editor({"a": ["1"]})
    await user.open("/_k2")
    captured["w"].add_key("b", ["2"])
    assert captured["w"].value == {"a": ["1"], "b": ["2"]}
    captured["w"].remove_key("a")
    assert captured["w"].value == {"b": ["2"]}


async def test_keyed_chip_editor_nested_change_propagates(user: User):
    captured = {}

    @ui.page("/_k3")
    def _page():
        captured["w"] = keyed_chip_editor({"k": ["x"]})
    await user.open("/_k3")
    captured["w"].set_values("k", ["x", "y"])
    assert captured["w"].value == {"k": ["x", "y"]}
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_widgets.py -k keyed -v
```
Expected: FAIL — `ImportError: keyed_chip_editor`

- [ ] **Step 3: Implement `keyed_chip_editor`**

Append to `not_dot_net/frontend/widgets.py`:

```python
class KeyedChipEditor:
    """Editor for `dict[str, list[str]]`.

    Renders a vertical stack of rows: `[key input | chip_list_editor | trash]`,
    plus an "Add" row at the bottom. The current value is exposed via the
    `value` property.
    """

    def __init__(self, value: dict[str, list[str]], *, key_label: str = "Key"):
        self._key_label = key_label
        self._rows: dict[str, dict] = {}
        self._container = ui.column().classes("w-full gap-2")
        with self._container:
            for k, vs in (value or {}).items():
                self._add_row(k, list(vs))
            self._add_button = ui.button("+ Add", on_click=self._on_add).props("flat dense color=primary")

    @property
    def value(self) -> dict[str, list[str]]:
        return {row["key_input"].value: list(row["chip"].value) for row in self._rows.values()}

    def add_key(self, key: str, values: list[str] | None = None) -> None:
        with self._container:
            self._add_row(key, values or [])
            self._add_button.move(self._container)

    def remove_key(self, key: str) -> None:
        row = self._rows.pop(key, None)
        if row:
            row["container"].delete()

    def set_values(self, key: str, values: list[str]) -> None:
        row = self._rows.get(key)
        if row:
            row["chip"].value = list(values)

    def _add_row(self, key: str, values: list[str]):
        row_container = ui.row().classes("w-full items-center gap-2 no-wrap")
        with row_container:
            key_input = ui.input(label=self._key_label, value=key).props("dense outlined").classes("w-40")
            chip = chip_list_editor(values)
            ui.button(icon="delete", on_click=lambda k=key: self.remove_key(k)).props("flat dense round color=negative")
        self._rows[key] = {"container": row_container, "key_input": key_input, "chip": chip}

    def _on_add(self):
        new_key = f"key_{len(self._rows) + 1}"
        self.add_key(new_key, [])


def keyed_chip_editor(value: dict[str, list[str]], *, key_label: str = "Key") -> KeyedChipEditor:
    return KeyedChipEditor(value, key_label=key_label)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_widgets.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/widgets.py tests/test_widgets.py
git commit -m "feat(widgets): add keyed_chip_editor for dict[str, list[str]] settings"
```

---

### Task 3: Wire chip widgets into `_render_form` + simplify `_is_complex`

**Files:**
- Modify: `not_dot_net/frontend/admin_settings.py:24-32` (`_is_complex`)
- Modify: `not_dot_net/frontend/admin_settings.py:62-126` (`_render_form` dispatch and save)
- Modify: `tests/test_app_settings.py:43-49` (assertion for dict-only schema)

- [ ] **Step 1: Adjust the failing test to reflect new semantics**

`_is_complex` should now flag a section as complex only if it has a nested `BaseModel`, not merely a dict. Replace the existing test:

```python
# tests/test_app_settings.py — replace test_admin_settings_detects_complex_schema_for_dict_fields
def test_admin_settings_dict_str_list_str_is_not_complex():
    """dict[str, list[str]] is now editable via keyed_chip_editor — not complex."""
    from not_dot_net.frontend.admin_settings import _is_complex

    class DictSettings(BaseModel):
        values: dict[str, list[str]] = {}

    assert _is_complex(DictSettings) is False


def test_admin_settings_nested_basemodel_still_complex():
    from not_dot_net.frontend.admin_settings import _is_complex

    class Inner(BaseModel):
        x: int = 0

    class Outer(BaseModel):
        nested: dict[str, Inner] = {}

    assert _is_complex(Outer) is True
```

(Keep the `test_admin_settings_detects_complex_schema_for_nested_models` test as-is.)

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_app_settings.py -v
```
Expected: `test_admin_settings_dict_str_list_str_is_not_complex` FAILS (current `_is_complex` flags any dict).

- [ ] **Step 3: Update `_is_complex`**

Replace `_is_complex` in `not_dot_net/frontend/admin_settings.py`:

```python
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
```

- [ ] **Step 4: Add chip dispatch to `_render_form`**

Modify the per-field dispatch loop in `_render_form` (the `for field_name, field_info in schema.model_fields.items():` block). Add new branches and update save logic:

```python
# imports at top of file
from not_dot_net.frontend.widgets import chip_list_editor, keyed_chip_editor, KeyedChipEditor
```

Replace the dispatch block (currently lines 74-92) with:

```python
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
```

Replace the save logic (the `for field_name, field_info in schema.model_fields.items():` block inside `save()`) with:

```python
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
```

- [ ] **Step 5: Run admin_settings tests to verify they pass**

```bash
uv run pytest tests/test_app_settings.py tests/test_widgets.py -v
```
Expected: all pass.

- [ ] **Step 6: Run full test suite to catch regressions**

```bash
uv run pytest
```
Expected: all pass (note: 499 tests today, expect 499 + new widget tests).

- [ ] **Step 7: Commit**

```bash
git add not_dot_net/frontend/admin_settings.py tests/test_app_settings.py
git commit -m "feat(admin): use chip widgets for list[str] and dict[str, list[str]] fields"
```

---

### Task 4: Workflow editor scaffolding (dialog, button, working copy)

**Files:**
- Create: `not_dot_net/frontend/workflow_editor.py`
- Modify: `not_dot_net/frontend/admin_settings.py:46-56` (workflows section dispatch)
- Test: `tests/test_workflow_editor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_editor.py
"""Tests for the workflow form editor dialog."""

import pytest
from nicegui import ui
from nicegui.testing import User

from not_dot_net.backend.workflow_service import workflows_config, WorkflowsConfig
from not_dot_net.config import WorkflowConfig, WorkflowStepConfig


@pytest.fixture
async def admin_user():
    """Minimal user object with manage_settings permission."""
    from types import SimpleNamespace
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        email="admin@test",
        is_superuser=True,
        is_active=True,
        role="admin",
    )


async def test_open_dialog_clones_current_config(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "demo": WorkflowConfig(label="Demo", steps=[
            WorkflowStepConfig(key="s1", type="form"),
        ]),
    }))

    captured = {}

    @ui.page("/_we1")
    async def _page():
        dlg = await WorkflowEditorDialog.create(admin_user)
        captured["dlg"] = dlg

    await user.open("/_we1")
    dlg = captured["dlg"]
    assert "demo" in dlg.working_copy.workflows
    assert dlg.working_copy.workflows["demo"].steps[0].key == "s1"
    # Mutating the working copy must not touch the persisted config
    dlg.working_copy.workflows["demo"].label = "Mutated"
    persisted = await workflows_config.get()
    assert persisted.workflows["demo"].label == "Demo"


async def test_save_persists_working_copy(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "demo": WorkflowConfig(label="Demo", steps=[]),
    }))

    captured = {}

    @ui.page("/_we2")
    async def _page():
        dlg = await WorkflowEditorDialog.create(admin_user)
        captured["dlg"] = dlg

    await user.open("/_we2")
    dlg = captured["dlg"]
    dlg.working_copy.workflows["demo"].label = "Renamed"
    await dlg.save()

    persisted = await workflows_config.get()
    assert persisted.workflows["demo"].label == "Renamed"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_workflow_editor.py -v
```
Expected: FAIL — `ModuleNotFoundError: not_dot_net.frontend.workflow_editor`.

- [ ] **Step 3: Implement the dialog scaffolding**

```python
# not_dot_net/frontend/workflow_editor.py
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
                tabs = ui.toggle(["Form", "YAML"], value="Form").props("dense")
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
```

Add the i18n key (English then French — find both files via existing patterns):

```python
# not_dot_net/frontend/i18n.py — add to the EN dict
"workflows_editor": "Workflows editor",
# and to the FR dict
"workflows_editor": "Éditeur de workflows",
```

- [ ] **Step 4: Wire the "Edit workflows…" button into `admin_settings.py`**

Modify the `for prefix, cfg_section in sorted(registry.items()):` loop in `render()` so that the `workflows` prefix uses the new dialog rather than the YAML editor:

```python
        with ui.expansion(cfg_section.label, icon="settings").classes("w-full"):
            if prefix == "workflows":
                from not_dot_net.frontend.workflow_editor import open_workflow_editor
                wf_count = len((await cfg_section.get()).workflows)
                step_count = sum(len(w.steps) for w in (await cfg_section.get()).workflows.values())
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
```

Add i18n key `edit_workflows` ("Edit workflows…" / "Éditer les workflows…").

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_workflow_editor.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py not_dot_net/frontend/admin_settings.py not_dot_net/frontend/i18n.py tests/test_workflow_editor.py
git commit -m "feat(admin): add workflow editor dialog scaffolding"
```

---

### Task 5: Workflow tree (left pane) — render, select, add, delete, duplicate

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py` (tree rendering and add/delete/duplicate methods)
- Modify: `tests/test_workflow_editor.py` (new tests)

- [ ] **Step 1: Add failing tests**

Append to `tests/test_workflow_editor.py`:

```python
async def test_add_workflow(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={}))

    captured = {}

    @ui.page("/_tree1")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree1")
    dlg = captured["dlg"]
    dlg.add_workflow("new_wf")
    assert "new_wf" in dlg.working_copy.workflows
    assert dlg.working_copy.workflows["new_wf"].label == "new_wf"
    assert dlg.selected_workflow == "new_wf"


async def test_add_workflow_rejects_duplicate_key(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={"a": WorkflowConfig(label="A", steps=[])}))

    captured = {}

    @ui.page("/_tree2")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree2")
    dlg = captured["dlg"]
    with pytest.raises(ValueError):
        dlg.add_workflow("a")


async def test_add_workflow_rejects_invalid_slug(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={}))

    captured = {}

    @ui.page("/_tree3")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree3")
    dlg = captured["dlg"]
    with pytest.raises(ValueError):
        dlg.add_workflow("Has Spaces")


async def test_delete_workflow(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[]),
        "b": WorkflowConfig(label="B", steps=[]),
    }))

    captured = {}

    @ui.page("/_tree4")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree4")
    dlg = captured["dlg"]
    dlg.delete_workflow("a")
    assert "a" not in dlg.working_copy.workflows
    assert dlg.selected_workflow == "b"


async def test_duplicate_workflow_deep_copies_steps(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "src": WorkflowConfig(label="Source", steps=[
            WorkflowStepConfig(key="s1", type="form"),
        ]),
    }))

    captured = {}

    @ui.page("/_tree5")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree5")
    dlg = captured["dlg"]
    dlg.duplicate_workflow("src", "copy")
    assert "copy" in dlg.working_copy.workflows
    assert dlg.working_copy.workflows["copy"].steps[0].key == "s1"
    # Mutating copy must not touch source
    dlg.working_copy.workflows["copy"].steps[0].key = "renamed"
    assert dlg.working_copy.workflows["src"].steps[0].key == "s1"


async def test_add_step(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[]),
    }))

    captured = {}

    @ui.page("/_tree6")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree6")
    dlg = captured["dlg"]
    dlg.add_step("a", "step1")
    assert dlg.working_copy.workflows["a"].steps[0].key == "step1"
    assert dlg.working_copy.workflows["a"].steps[0].type == "form"


async def test_add_step_rejects_duplicate_within_workflow(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[WorkflowStepConfig(key="x", type="form")]),
    }))

    captured = {}

    @ui.page("/_tree7")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree7")
    dlg = captured["dlg"]
    with pytest.raises(ValueError):
        dlg.add_step("a", "x")


async def test_delete_step(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[
            WorkflowStepConfig(key="x", type="form"),
            WorkflowStepConfig(key="y", type="form"),
        ]),
    }))

    captured = {}

    @ui.page("/_tree8")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_tree8")
    dlg = captured["dlg"]
    dlg.delete_step("a", "x")
    keys = [s.key for s in dlg.working_copy.workflows["a"].steps]
    assert keys == ["y"]
```

- [ ] **Step 2: Run new tests to verify failure**

```bash
uv run pytest tests/test_workflow_editor.py -v
```
Expected: new 8 tests FAIL — methods don't exist.

- [ ] **Step 3: Implement the tree-mutation methods**

Add to `WorkflowEditorDialog` in `workflow_editor.py`:

```python
import re
from not_dot_net.config import WorkflowConfig, WorkflowStepConfig

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_slug(key: str) -> None:
    if not _SLUG_RE.fullmatch(key):
        raise ValueError(f"Invalid key '{key}': must be lowercase letters, digits, underscore; start with a letter")
```

```python
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
```

Replace the `_build` placeholder for tree/detail rendering — refactor into `_refresh_tree` and `_refresh_detail`:

```python
    def _build(self) -> None:
        self.dialog = ui.dialog().props("maximized")
        with self.dialog, ui.card().classes("w-full h-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(t("workflows_editor")).classes("text-h6")
            with ui.row().classes("w-full grow no-wrap"):
                self._tree_container = ui.column().classes("w-72 q-pr-md").style("border-right: 1px solid #e0e0e0")
                self._detail_container = ui.column().classes("grow")
            with ui.row().classes("w-full justify-end"):
                ui.button(t("cancel"), on_click=self.close).props("flat")
                ui.button(t("reset_defaults"), on_click=self.reset).props("flat color=grey")
                ui.button(t("save"), on_click=self.save).props("color=primary")
        self._refresh_tree()
        self._refresh_detail()

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
        self._detail_container.clear()
        with self._detail_container:
            if self.selected_workflow is None:
                ui.label("No workflow selected. Add one to begin.").classes("text-grey")
                return
            wf = self.working_copy.workflows[self.selected_workflow]
            if self.selected_step is None:
                ui.label(f"Workflow: {self.selected_workflow}").classes("text-h6")
                ui.label("(workflow-level editor will land in Task 6)").classes("text-grey")
            else:
                ui.label(f"Step: {self.selected_step}").classes("text-h6")
                ui.label("(step editor will land in Tasks 7-8)").classes("text-grey")
            ui.button(f"+ Add step to {self.selected_workflow}",
                      on_click=lambda k=self.selected_workflow: self._on_add_step_click(k)
                      ).props("flat dense color=primary")

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
            inp = ui.input(label="key").props("dense outlined autofocus")
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_workflow_editor.py -v
```
Expected: all pass (2 + 8 = 10 tests).

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py tests/test_workflow_editor.py
git commit -m "feat(workflow-editor): tree pane with add/delete/duplicate/select"
```

---

### Task 6: Workflow node editor (right pane when a workflow is selected)

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py` (`_refresh_detail` for workflow case)
- Modify: `tests/test_workflow_editor.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_workflow_editor.py`:

```python
async def test_workflow_label_edit_propagates(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="Original", steps=[]),
    }))

    captured = {}

    @ui.page("/_wf_edit1")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_wf_edit1")
    dlg = captured["dlg"]
    dlg.set_workflow_label("a", "New Label")
    assert dlg.working_copy.workflows["a"].label == "New Label"


async def test_workflow_target_email_field_edit(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[]),
    }))

    captured = {}

    @ui.page("/_wf_edit2")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_wf_edit2")
    dlg = captured["dlg"]
    dlg.set_workflow_field("a", "target_email_field", "target_email")
    assert dlg.working_copy.workflows["a"].target_email_field == "target_email"


async def test_add_notification_rule(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    from not_dot_net.config import NotificationRuleConfig
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[]),
    }))

    captured = {}

    @ui.page("/_wf_edit3")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_wf_edit3")
    dlg = captured["dlg"]
    dlg.add_notification_rule("a")
    assert len(dlg.working_copy.workflows["a"].notifications) == 1
    rule = dlg.working_copy.workflows["a"].notifications[0]
    assert rule.event == ""
    assert rule.notify == []


async def test_delete_notification_rule(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    from not_dot_net.config import NotificationRuleConfig
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[], notifications=[
            NotificationRuleConfig(event="submit", notify=["admin"]),
            NotificationRuleConfig(event="reject", notify=["requester"]),
        ]),
    }))

    captured = {}

    @ui.page("/_wf_edit4")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_wf_edit4")
    dlg = captured["dlg"]
    dlg.delete_notification_rule("a", 0)
    assert len(dlg.working_copy.workflows["a"].notifications) == 1
    assert dlg.working_copy.workflows["a"].notifications[0].event == "reject"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_workflow_editor.py -k "wf_edit or notification" -v
```
Expected: 4 new tests FAIL.

- [ ] **Step 3: Implement workflow-level mutators and editor render**

Add to `WorkflowEditorDialog`:

```python
from not_dot_net.config import NotificationRuleConfig
```

```python
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
```

Replace the workflow-selected branch of `_refresh_detail`:

```python
    def _refresh_detail(self) -> None:
        if self._detail_container is None:
            return
        self._detail_container.clear()
        with self._detail_container:
            if self.selected_workflow is None:
                ui.label("No workflow selected. Add one to begin.").classes("text-grey")
                return
            wf = self.working_copy.workflows[self.selected_workflow]
            if self.selected_step is None:
                self._render_workflow_editor(self.selected_workflow, wf)
            else:
                ui.label(f"Step: {self.selected_step}").classes("text-h6")
                ui.label("(step editor will land in Tasks 7-8)").classes("text-grey")
```

```python
    def _render_workflow_editor(self, wf_key: str, wf) -> None:
        from not_dot_net.frontend.widgets import keyed_chip_editor, chip_list_editor

        ui.label(f"Workflow: {wf_key}").classes("text-h6")

        ui.input(t("label"), value=wf.label,
                 on_change=lambda e, k=wf_key: self.set_workflow_label(k, e.value)
                 ).classes("w-full").props("dense outlined")

        ui.input("start_role", value=wf.start_role or "",
                 on_change=lambda e, k=wf_key: self.set_workflow_field(k, "start_role", e.value)
                 ).classes("w-full").props("dense outlined").tooltip("Role key required to start this workflow")

        ui.input("target_email_field", value=wf.target_email_field or "",
                 on_change=lambda e, k=wf_key: self.set_workflow_field(k, "target_email_field", e.value or None)
                 ).classes("w-full").props("dense outlined").tooltip(
                     "Name of the field whose value is the target person's email")

        ui.label("Document instructions").classes("text-subtitle2 q-mt-md")
        di = keyed_chip_editor(wf.document_instructions or {}, key_label="status")
        # bind back: refresh on every change is too aggressive; instead read on save via _collect()
        self._workflow_doc_instructions_widget = (wf_key, di)

        ui.label("Notification rules").classes("text-subtitle2 q-mt-md")
        self._render_notification_table(wf_key, wf)

    def _render_notification_table(self, wf_key: str, wf) -> None:
        from not_dot_net.frontend.widgets import chip_list_editor

        step_keys = [s.key for s in wf.steps]
        action_suggestions = sorted({a for s in wf.steps for a in s.actions} | {"submit", "approve", "reject", "request_corrections"})
        notify_suggestions = ["requester", "target_person"]  # role keys filled in below if available

        for idx, rule in enumerate(wf.notifications):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                event_input = ui.select(
                    options=action_suggestions, value=rule.event or None,
                    new_value_mode="add-unique", with_input=True,
                    on_change=lambda e, i=idx, k=wf_key: setattr(self.working_copy.workflows[k].notifications[i], "event", e.value or ""),
                ).props("dense outlined").classes("w-40")
                ui.select(
                    options=[None, *step_keys], value=rule.step,
                    label="step",
                    on_change=lambda e, i=idx, k=wf_key: setattr(self.working_copy.workflows[k].notifications[i], "step", e.value),
                ).props("dense outlined").classes("w-40")
                notify_widget = chip_list_editor(rule.notify, suggestions=notify_suggestions)

                def _bind_notify(w=notify_widget, i=idx, k=wf_key):
                    self.working_copy.workflows[k].notifications[i].notify = list(w.value)
                notify_widget.on_value_change(lambda e, _b=_bind_notify: _b())

                ui.button(icon="delete",
                          on_click=lambda i=idx, k=wf_key: self.delete_notification_rule(k, i)
                          ).props("flat dense round color=negative")

        ui.button("+ Add notification rule",
                  on_click=lambda k=wf_key: self.add_notification_rule(k)
                  ).props("flat dense color=primary")
```

Add a `_collect_widgets()` step in `save()` to pull document_instructions value from the keyed_chip_editor before validation:

```python
    def _collect_widget_state(self) -> None:
        wf_doc = getattr(self, "_workflow_doc_instructions_widget", None)
        if wf_doc:
            wf_key, widget = wf_doc
            if wf_key in self.working_copy.workflows:
                self.working_copy.workflows[wf_key].document_instructions = widget.value
```

Update `save()`:

```python
    async def save(self) -> None:
        self._collect_widget_state()
        try:
            validated = WorkflowsConfig.model_validate(self.working_copy.model_dump())
        except ValidationError as e:
            ui.notify(str(e), color="negative", multi_line=True)
            return
        ...
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_workflow_editor.py -v
```
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py tests/test_workflow_editor.py
git commit -m "feat(workflow-editor): right-pane editor for workflow-level fields and notifications"
```

---

### Task 7: Step node editor — top fields (key, type, assignee mode, actions, partial_save, corrections_target)

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py` (step branch of `_refresh_detail`)
- Modify: `tests/test_workflow_editor.py`

- [ ] **Step 1: Add failing tests**

```python
async def test_set_step_key_renames_in_place(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[
            WorkflowStepConfig(key="old", type="form"),
        ]),
    }))

    captured = {}

    @ui.page("/_step1")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_step1")
    dlg = captured["dlg"]
    dlg.set_step_field("a", "old", "key", "renamed")
    assert dlg.working_copy.workflows["a"].steps[0].key == "renamed"
    assert dlg.selected_step == "renamed"


async def test_set_step_assignee_mode_role(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[
            WorkflowStepConfig(key="s", type="form", assignee_role=None,
                               assignee_permission="approve_workflows"),
        ]),
    }))

    captured = {}

    @ui.page("/_step2")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_step2")
    dlg = captured["dlg"]
    dlg.set_step_assignee("a", "s", mode="role", value="director")
    step = dlg.working_copy.workflows["a"].steps[0]
    assert step.assignee_role == "director"
    assert step.assignee_permission is None
    assert step.assignee is None


async def test_set_step_assignee_mode_permission(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[
            WorkflowStepConfig(key="s", type="form", assignee_role="staff"),
        ]),
    }))

    captured = {}

    @ui.page("/_step3")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_step3")
    dlg = captured["dlg"]
    dlg.set_step_assignee("a", "s", mode="permission", value="approve_workflows")
    step = dlg.working_copy.workflows["a"].steps[0]
    assert step.assignee_role is None
    assert step.assignee_permission == "approve_workflows"
    assert step.assignee is None


async def test_set_step_assignee_mode_contextual(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[WorkflowStepConfig(key="s", type="form")]),
    }))

    captured = {}

    @ui.page("/_step4")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_step4")
    dlg = captured["dlg"]
    dlg.set_step_assignee("a", "s", mode="contextual", value="target_person")
    step = dlg.working_copy.workflows["a"].steps[0]
    assert step.assignee == "target_person"
    assert step.assignee_role is None
    assert step.assignee_permission is None
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_workflow_editor.py -k "step" -v
```
Expected: new tests FAIL — methods don't exist.

- [ ] **Step 3: Implement step mutators and step-editor render**

Add to `WorkflowEditorDialog`:

```python
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
```

Replace the step-selected branch of `_refresh_detail`:

```python
            else:
                step = self._find_step(self.selected_workflow, self.selected_step)
                self._render_step_editor(self.selected_workflow, step)
```

```python
    def _render_step_editor(self, wf_key: str, step) -> None:
        from not_dot_net.frontend.widgets import chip_list_editor

        ui.label(f"Step: {step.key}").classes("text-h6")

        ui.input("key", value=step.key,
                 on_change=lambda e, w=wf_key, k=step.key: self._safe_set(w, k, "key", e.value)
                 ).classes("w-full").props("dense outlined")

        ui.select(["form", "approval"], value=step.type, label="type",
                  on_change=lambda e, w=wf_key, k=step.key: self.set_step_field(w, k, "type", e.value)
                  ).classes("w-full").props("dense outlined")

        # Assignee — radio group
        current_mode = ("role" if step.assignee_role else
                        "permission" if step.assignee_permission else
                        "contextual" if step.assignee else "role")
        current_value = step.assignee_role or step.assignee_permission or step.assignee or ""

        ui.label("Assigned to").classes("text-subtitle2 q-mt-sm")
        mode_toggle = ui.toggle({"role": "Role", "permission": "Permission", "contextual": "Contextual"},
                                value=current_mode).props("dense")
        value_input = ui.input("assignee value", value=current_value).classes("w-full").props("dense outlined")

        def _commit_assignee(w=wf_key, k=step.key):
            self.set_step_assignee(w, k, mode=mode_toggle.value, value=value_input.value or None)

        mode_toggle.on_value_change(lambda e: _commit_assignee())
        value_input.on_value_change(lambda e: _commit_assignee())

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
                      ).classes("w-full").props("dense outlined")

    def _safe_set(self, wf_key: str, step_key: str, field: str, value):
        try:
            self.set_step_field(wf_key, step_key, field, value)
        except ValueError as e:
            ui.notify(str(e), color="negative")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_workflow_editor.py -v
```
Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py tests/test_workflow_editor.py
git commit -m "feat(workflow-editor): step editor with assignee mode toggle and actions"
```

---

### Task 8: Step fields table

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py` (extend `_render_step_editor`)
- Modify: `tests/test_workflow_editor.py`

- [ ] **Step 1: Add failing tests**

```python
async def test_add_field_to_step(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[WorkflowStepConfig(key="s", type="form")]),
    }))

    captured = {}

    @ui.page("/_field1")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_field1")
    dlg = captured["dlg"]
    dlg.add_field("a", "s")
    fields = dlg.working_copy.workflows["a"].steps[0].fields
    assert len(fields) == 1
    assert fields[0].name == ""
    assert fields[0].type == "text"


async def test_set_field_attr(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    from not_dot_net.config import FieldConfig
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[
            WorkflowStepConfig(key="s", type="form", fields=[
                FieldConfig(name="email", type="email"),
            ]),
        ]),
    }))

    captured = {}

    @ui.page("/_field2")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_field2")
    dlg = captured["dlg"]
    dlg.set_field_attr("a", "s", 0, "required", True)
    dlg.set_field_attr("a", "s", 0, "label", "target_email")
    field = dlg.working_copy.workflows["a"].steps[0].fields[0]
    assert field.required is True
    assert field.label == "target_email"


async def test_delete_field(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    from not_dot_net.config import FieldConfig
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[
            WorkflowStepConfig(key="s", type="form", fields=[
                FieldConfig(name="x", type="text"),
                FieldConfig(name="y", type="text"),
            ]),
        ]),
    }))

    captured = {}

    @ui.page("/_field3")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_field3")
    dlg = captured["dlg"]
    dlg.delete_field("a", "s", 0)
    fields = dlg.working_copy.workflows["a"].steps[0].fields
    assert [f.name for f in fields] == ["y"]


async def test_org_list_keys_introspected():
    """The options_key dropdown is populated from OrgConfig list[str] fields."""
    from not_dot_net.frontend.workflow_editor import _org_list_field_names
    keys = _org_list_field_names()
    assert "teams" in keys
    assert "sites" in keys
    assert "employment_statuses" in keys
    assert "app_name" not in keys  # not a list[str]
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_workflow_editor.py -k field -v
```
Expected: FAIL — `add_field`/`set_field_attr`/`delete_field`/`_org_list_field_names` not defined.

- [ ] **Step 3: Implement field mutators + helper + rendering**

Add to `workflow_editor.py`:

```python
from not_dot_net.config import FieldConfig, OrgConfig


def _org_list_field_names() -> list[str]:
    return [
        name for name, info in OrgConfig.model_fields.items()
        if info.annotation == list[str]
    ]
```

Add to `WorkflowEditorDialog`:

```python
    def add_field(self, wf_key: str, step_key: str) -> None:
        step = self._find_step(wf_key, step_key)
        step.fields.append(FieldConfig(name="", type="text"))
        self._refresh_detail()

    def set_field_attr(self, wf_key: str, step_key: str, index: int, attr: str, value) -> None:
        step = self._find_step(wf_key, step_key)
        setattr(step.fields[index], attr, value)

    def delete_field(self, wf_key: str, step_key: str, index: int) -> None:
        step = self._find_step(wf_key, step_key)
        del step.fields[index]
        self._refresh_detail()
```

Append to `_render_step_editor` (after the `corrections_target` block):

```python
        ui.label("Fields").classes("text-subtitle2 q-mt-md")
        org_keys = [None, *_org_list_field_names()]
        for idx, field in enumerate(step.fields):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.input("name", value=field.name,
                         on_change=lambda e, i=idx, w=wf_key, sk=step.key: self.set_field_attr(w, sk, i, "name", e.value)
                         ).props("dense outlined").classes("w-32")
                ui.select(["text", "email", "textarea", "date", "select", "file"], value=field.type, label="type",
                          on_change=lambda e, i=idx, w=wf_key, sk=step.key: self.set_field_attr(w, sk, i, "type", e.value)
                          ).props("dense outlined").classes("w-32")
                ui.switch("required", value=field.required,
                          on_change=lambda e, i=idx, w=wf_key, sk=step.key: self.set_field_attr(w, sk, i, "required", e.value))
                ui.input("label", value=field.label,
                         on_change=lambda e, i=idx, w=wf_key, sk=step.key: self.set_field_attr(w, sk, i, "label", e.value)
                         ).props("dense outlined").classes("w-40")
                ui.select(org_keys, value=field.options_key, label="options_key",
                          on_change=lambda e, i=idx, w=wf_key, sk=step.key: self.set_field_attr(w, sk, i, "options_key", e.value)
                          ).props("dense outlined").classes("w-40")
                ui.switch("encrypted", value=field.encrypted,
                          on_change=lambda e, i=idx, w=wf_key, sk=step.key: self.set_field_attr(w, sk, i, "encrypted", e.value))
                ui.switch("half_width", value=field.half_width,
                          on_change=lambda e, i=idx, w=wf_key, sk=step.key: self.set_field_attr(w, sk, i, "half_width", e.value))
                ui.button(icon="delete",
                          on_click=lambda i=idx, w=wf_key, sk=step.key: self.delete_field(w, sk, i)
                          ).props("flat dense round color=negative")

        ui.button("+ Add field",
                  on_click=lambda w=wf_key, sk=step.key: self.add_field(w, sk)
                  ).props("flat dense color=primary")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_workflow_editor.py -v
```
Expected: 22 passed.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py tests/test_workflow_editor.py
git commit -m "feat(workflow-editor): step fields table"
```

---

### Task 9: YAML tab with bidirectional sync

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py` (tabs + YAML view)
- Modify: `tests/test_workflow_editor.py`

- [ ] **Step 1: Add failing tests**

```python
async def test_yaml_dump_reflects_working_copy(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="Renamed", steps=[]),
    }))

    captured = {}

    @ui.page("/_yaml1")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_yaml1")
    dlg = captured["dlg"]
    yaml_str = dlg.dump_yaml()
    assert "Renamed" in yaml_str
    assert "workflows:" in yaml_str


async def test_yaml_apply_updates_working_copy(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[]),
    }))

    captured = {}

    @ui.page("/_yaml2")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_yaml2")
    dlg = captured["dlg"]
    new_yaml = """
token_expiry_days: 60
verification_code_expiry_minutes: 15
max_upload_size_mb: 10
workflows:
  a:
    label: From YAML
    start_role: staff
    steps: []
    notifications: []
    document_instructions: {}
"""
    dlg.apply_yaml(new_yaml)
    assert dlg.working_copy.workflows["a"].label == "From YAML"
    assert dlg.working_copy.token_expiry_days == 60


async def test_yaml_apply_invalid_raises(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={}))

    captured = {}

    @ui.page("/_yaml3")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_yaml3")
    dlg = captured["dlg"]
    with pytest.raises(ValueError):
        dlg.apply_yaml("not: [valid yaml structure for the schema")
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_workflow_editor.py -k yaml -v
```
Expected: FAIL — `dump_yaml`/`apply_yaml` not defined.

- [ ] **Step 3: Implement YAML methods**

Add to `workflow_editor.py`:

```python
from yaml import safe_dump, safe_load
```

In `WorkflowEditorDialog`:

```python
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
```

Wire the tabs into `_build`. Replace the body section (between header row and footer) with:

```python
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
```

Add `self._yaml_editor = None` to `__init__` and a `_current_tab` tracker:

```python
    def _on_tab_change(self, e):
        new_tab = e.value
        if new_tab == "YAML":
            # leaving Form: collect widget state, dump
            self._collect_widget_state()
            if self._yaml_editor is not None:
                self._yaml_editor.value = self.dump_yaml()
        elif new_tab == "Form":
            # leaving YAML: apply, refresh
            if self._yaml_editor is not None:
                try:
                    self.apply_yaml(self._yaml_editor.value)
                except ValueError as e:
                    ui.notify(f"Invalid YAML: {e}", color="negative", multi_line=True)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_workflow_editor.py -v
```
Expected: 25 passed.

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py tests/test_workflow_editor.py
git commit -m "feat(workflow-editor): yaml tab with form↔yaml sync"
```

---

### Task 10: Cross-field validation hints, dirty-state cancel, end-to-end test

**Files:**
- Modify: `not_dot_net/frontend/workflow_editor.py`
- Modify: `tests/test_workflow_editor.py`

- [ ] **Step 1: Add failing tests**

```python
async def test_validation_warnings_step_key_collision(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[
            WorkflowStepConfig(key="x", type="form"),
            WorkflowStepConfig(key="x", type="form"),
        ]),
    }))
    captured = {}

    @ui.page("/_warn1")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_warn1")
    dlg = captured["dlg"]
    warnings = dlg.compute_warnings()
    assert any("duplicate step" in w.lower() for w in warnings)


async def test_validation_warnings_dangling_corrections_target(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[
            WorkflowStepConfig(key="x", type="form",
                               actions=["request_corrections"],
                               corrections_target="nope"),
        ]),
    }))
    captured = {}

    @ui.page("/_warn2")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_warn2")
    dlg = captured["dlg"]
    warnings = dlg.compute_warnings()
    assert any("corrections_target" in w for w in warnings)


async def test_validation_warnings_target_email_field_missing(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", target_email_field="missing", steps=[]),
    }))
    captured = {}

    @ui.page("/_warn3")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_warn3")
    dlg = captured["dlg"]
    warnings = dlg.compute_warnings()
    assert any("target_email_field" in w for w in warnings)


async def test_dirty_flag_tracks_changes(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[]),
    }))
    captured = {}

    @ui.page("/_dirty1")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_dirty1")
    dlg = captured["dlg"]
    assert dlg.is_dirty() is False
    dlg.set_workflow_label("a", "Mutated")
    assert dlg.is_dirty() is True


async def test_save_invalid_does_not_persist(user: User, admin_user):
    """A working copy that fails Pydantic validation should not be saved."""
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[]),
    }))
    captured = {}

    @ui.page("/_save_invalid")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_save_invalid")
    dlg = captured["dlg"]
    # Force an invalid state by injecting an invalid step type.
    dlg.working_copy.workflows["a"].steps.append(
        WorkflowStepConfig.model_construct(key="bad", type="not-a-real-type")
    )
    await dlg.save()
    persisted = await workflows_config.get()
    assert persisted.workflows["a"].steps == []  # save was rejected, original preserved


async def test_audit_log_emitted_on_save(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    from not_dot_net.backend.audit import list_audit_events
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[]),
    }))
    captured = {}

    @ui.page("/_audit1")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_audit1")
    dlg = captured["dlg"]
    dlg.set_workflow_label("a", "Edited")
    await dlg.save()

    events = await list_audit_events(limit=10)
    assert any(e.entity == "settings" and e.action == "update"
               and (e.detail or "").startswith("section=workflows")
               for e in events)
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_workflow_editor.py -k "warn or dirty or save_invalid or audit" -v
```
Expected: FAIL — methods not defined.

- [ ] **Step 3: Implement validation, dirty tracking, and warnings panel**

Add to `WorkflowEditorDialog`:

```python
    def is_dirty(self) -> bool:
        self._collect_widget_state()
        return self.working_copy.model_dump() != self.original.model_dump()

    def compute_warnings(self) -> list[str]:
        warnings: list[str] = []
        org_list_keys = set(_org_list_field_names())
        for wf_key, wf in self.working_copy.workflows.items():
            seen_step_keys: set[str] = set()
            step_keys: list[str] = []
            for step in wf.steps:
                if step.key in seen_step_keys:
                    warnings.append(f"[{wf_key}] duplicate step key '{step.key}'")
                seen_step_keys.add(step.key)
                step_keys.append(step.key)
            field_names = {f.name for s in wf.steps for f in s.fields}
            if wf.target_email_field and wf.target_email_field not in field_names:
                warnings.append(f"[{wf_key}] target_email_field '{wf.target_email_field}' does not match any field name")
            for step in wf.steps:
                if "request_corrections" in (step.actions or []):
                    if step.corrections_target and step.corrections_target not in step_keys:
                        warnings.append(f"[{wf_key}/{step.key}] corrections_target '{step.corrections_target}' does not exist")
                for f in step.fields:
                    if f.options_key and f.options_key not in org_list_keys:
                        warnings.append(f"[{wf_key}/{step.key}/{f.name}] options_key '{f.options_key}' is not an OrgConfig list field")
            for nr in wf.notifications:
                if nr.step and nr.step not in step_keys:
                    warnings.append(f"[{wf_key}] notification rule references missing step '{nr.step}'")
        return warnings
```

Render the warnings pill in the footer (modify the footer row in `_build`):

```python
            with ui.row().classes("w-full justify-between items-center"):
                self._warnings_label = ui.label("").classes("text-warning text-sm")
                with ui.row():
                    ui.button(t("cancel"), on_click=self._on_cancel_click).props("flat")
                    ui.button(t("reset_defaults"), on_click=self.reset).props("flat color=grey")
                    ui.button(t("save"), on_click=self.save).props("color=primary")
```

Refresh the warnings label whenever the detail re-renders. Append to `_refresh_detail`:

```python
        if hasattr(self, "_warnings_label") and self._warnings_label is not None:
            warnings = self.compute_warnings()
            if warnings:
                self._warnings_label.set_text(f"⚠ {len(warnings)} issue(s) — click to view")
                self._warnings_label.on("click", lambda e, ws=warnings: self._show_warnings(ws))
                self._warnings_label.classes(replace="text-warning text-sm cursor-pointer")
            else:
                self._warnings_label.set_text("")

    def _show_warnings(self, warnings: list[str]) -> None:
        dlg = ui.dialog()
        with dlg, ui.card():
            ui.label("Configuration warnings").classes("text-h6")
            for w in warnings:
                ui.label(f"• {w}")
            ui.button("Close", on_click=dlg.close).props("flat")
        dlg.open()
```

Cancel-with-confirm:

```python
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
```

- [ ] **Step 4: Run all tests**

```bash
uv run pytest
```
Expected: full suite passes (target: 499 + 28 new = 527).

- [ ] **Step 5: Manual smoke test**

Start the dev server and exercise the UI golden path:

```bash
uv run python -m not_dot_net.cli serve --host localhost --port 8088
```

Then in a browser:
1. Log in as admin (auto-created in dev).
2. Open Settings tab → expand Workflows.
3. Click "Edit workflows…". Confirm dialog opens.
4. Select an existing workflow → edit label → switch step → toggle a field's `required` switch → click Save.
5. Reopen dialog → confirm changes persisted.
6. Switch to YAML tab → confirm YAML reflects edits → modify a value → switch back to Form → confirm refreshed.
7. Add a workflow → add a step → fill fields → Save.
8. Make a change → Cancel → confirm "Discard unsaved changes?" appears.

If the smoke test reveals issues, fix and re-run `uv run pytest`. Repeat until clean.

- [ ] **Step 6: Commit**

```bash
git add not_dot_net/frontend/workflow_editor.py tests/test_workflow_editor.py
git commit -m "feat(workflow-editor): cross-field warnings, dirty-state cancel, audit on save"
```

---

## Self-review checklist (for the engineer)

After all tasks pass:

1. `uv run pytest` — full suite green.
2. Read `not_dot_net/frontend/workflow_editor.py` end to end. Anything that grew beyond ~400 lines is a smell — consider extracting render helpers but **do not refactor for the sake of it**.
3. Confirm no YAML editor reachable for `workflows` from the settings UI (it lives only inside the dialog as the YAML tab).
4. Confirm `BookingsConfig.software_tags` renders as a keyed chip editor (manual — open Settings → Bookings).
5. Confirm `OrgConfig.teams` renders as a chip editor (manual — open Settings → Organization).
6. Update `MEMORY.md` with a one-line note in the user's auto-memory: "Workflow form editor: master-detail dialog in `frontend/workflow_editor.py`, YAML tab kept inside as escape hatch".
