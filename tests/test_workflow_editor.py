"""Tests for the workflow form editor dialog."""

import pytest
from nicegui import ui
from nicegui.testing import User

from not_dot_net.backend.workflow_service import workflows_config, WorkflowsConfig
from not_dot_net.config import FieldConfig, NotificationRuleConfig, WorkflowConfig, WorkflowStepConfig


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
    dlg.select("a", "old")
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
    # Force an invalid state by injecting a step with an invalid type via model_construct
    dlg.working_copy.workflows["a"].steps.append(
        WorkflowStepConfig.model_construct(key="bad", type=123)  # type must be str
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
    assert any(e.category == "settings" and e.action == "update"
               and (e.detail or "").startswith("section=workflows")
               for e in events)


async def test_save_applies_pending_yaml_when_yaml_tab_active(user: User, admin_user):
    """If user edits YAML and clicks Save without switching to Form, the YAML edits should still take effect."""
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="Original", steps=[]),
    }))
    captured = {}

    @ui.page("/_yaml_save")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_yaml_save")
    dlg = captured["dlg"]
    # Simulate user being on the YAML tab with edits
    dlg._active_tab = "YAML"
    dlg._yaml_editor.value = """
token_expiry_days: 30
verification_code_expiry_minutes: 15
max_upload_size_mb: 10
workflows:
  a:
    label: From YAML Save
    start_role: staff
    steps: []
    notifications: []
    document_instructions: {}
"""
    await dlg.save()
    persisted = await workflows_config.get()
    assert persisted.workflows["a"].label == "From YAML Save"


async def test_step_rename_can_be_repeated_without_keyerror(user: User, admin_user):
    """A second keystroke in the step-key input must not raise KeyError.

    The bug: the detail pane's on_change closure captures k=step.key at render
    time. After rename 1 (old→n), the pane is NOT rebuilt, so keystroke 2 fires
    _safe_set(wf, "old", "key", "ne") — but "old" no longer exists → KeyError
    escaping _safe_set (which only catches ValueError).
    The fix is to call _refresh_detail() inside set_step_field for field=="key".
    """
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[
            WorkflowStepConfig(key="old", type="form"),
        ]),
    }))
    captured = {}

    @ui.page("/_rename_keyerror")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_rename_keyerror")
    dlg = captured["dlg"]
    dlg.select("a", "old")
    # Rename 1: old → n (stale closure captures k="old")
    dlg._safe_set("a", "old", "key", "n")
    assert dlg.working_copy.workflows["a"].steps[0].key == "n"
    # Rename 2: simulates a stale UI closure still holding k="old".
    # Without the fix this raises KeyError (uncaught by _safe_set).
    # With the fix _refresh_detail rebuilt the pane, but we call with stale key
    # to verify _safe_set now swallows the KeyError too OR _refresh_detail prevents it.
    # We test the actual behaviour: after fix the step key ended up as "ne".
    dlg._safe_set("a", "old", "key", "ne")   # stale k="old" — the real bug scenario
    # With fix: _refresh_detail was called after rename 1, rebuilding closures with k="n".
    # The stale call _safe_set("a","old",...) should be silently swallowed (key not found).
    # The step should still be at "n" (stale write ignored) and selected_step="n".
    assert dlg.working_copy.workflows["a"].steps[0].key == "n"
    assert dlg.selected_step == "n"


async def test_reset_refreshes_ui_and_logs_audit(user: User, admin_user):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    from not_dot_net.backend.audit import list_audit_events
    # Seed with a non-default workflow so reset is observable
    await workflows_config.set(WorkflowsConfig(workflows={
        "custom": WorkflowConfig(label="Custom", steps=[]),
    }))
    captured = {}

    @ui.page("/_reset1")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_reset1")
    dlg = captured["dlg"]
    assert "custom" in dlg.working_copy.workflows
    await dlg.reset()
    # Working copy now matches defaults (vpn_access + onboarding from WorkflowsConfig defaults)
    assert "custom" not in dlg.working_copy.workflows
    # Selection updated to first default workflow (or None if defaults are empty)
    assert dlg.selected_step is None
    # Audit log entry emitted
    events = await list_audit_events(limit=10)
    assert any(e.category == "settings" and e.action == "reset"
               and (e.detail or "").startswith("section=workflows")
               for e in events)


async def test_doc_instructions_survive_navigation(user: User, admin_user):
    """Editing document_instructions then switching workflows must not lose the edits."""
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(WorkflowsConfig(workflows={
        "a": WorkflowConfig(label="A", steps=[], document_instructions={}),
        "b": WorkflowConfig(label="B", steps=[]),
    }))
    captured = {}

    @ui.page("/_doc_nav")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin_user)

    await user.open("/_doc_nav")
    dlg = captured["dlg"]
    dlg.select("a")  # workflow editor renders with doc_instructions widget
    # Simulate the user adding an entry to the doc_instructions widget
    if dlg._workflow_doc_instructions_widget:
        _, widget = dlg._workflow_doc_instructions_widget
        widget.add_key("intern", ["passport", "id"])
    # Navigate away (this previously lost the edit)
    dlg.select("b")
    # Navigate back
    dlg.select("a")
    assert dlg.working_copy.workflows["a"].document_instructions.get("intern") == ["passport", "id"]
