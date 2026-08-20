import logging

import pytest

from not_dot_net.config import FieldConfig, FieldRef, WorkflowStepConfig, resolve_field_ref, WorkflowConfig
from not_dot_net.backend.field_definitions import (
    FieldDefinition, FieldDefinitionsConfig, resolve_step_fields,
    field_definitions_config, save_field_definition, delete_field_definition,
    FieldDefinitionInUse, definition_usages,
)
from not_dot_net.backend.workflow_service import _filter_step_data
from not_dot_net.backend.workflow_config import workflows_config, WorkflowsConfig
from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog


def test_resolve_inherits_unset_properties():
    defn = FieldDefinition(key="phone", type="phone", label="Phone",
                           required=True, half_width=True)
    resolved = resolve_field_ref(FieldRef(ref="phone"), defn)
    assert resolved.name == "phone"
    assert resolved.type == "phone"
    assert resolved.label == "Phone"
    assert resolved.required is True
    assert resolved.half_width is True
    assert resolved.visible_when is None


def test_resolve_override_to_false_beats_inherit():
    defn = FieldDefinition(key="phone", type="phone", required=True)
    resolved = resolve_field_ref(FieldRef(ref="phone", required=False), defn)
    assert resolved.required is False


def test_resolve_name_not_overridable_and_visible_when_is_local():
    defn = FieldDefinition(key="phone", type="phone")
    ref = FieldRef(ref="phone", visible_when={"needs": True})
    resolved = resolve_field_ref(ref, defn)
    assert resolved.name == "phone"
    assert resolved.visible_when == {"needs": True}


def test_step_fields_union_deserializes_both_shapes():
    step = WorkflowStepConfig.model_validate({
        "key": "s1", "type": "form",
        "fields": [
            {"name": "note", "type": "text"},
            {"ref": "phone", "required": True},
        ],
    })
    assert isinstance(step.fields[0], FieldConfig)
    assert isinstance(step.fields[1], FieldRef)
    assert step.fields[1].ref == "phone"
    assert step.fields[1].required is True


async def test_resolve_step_fields_mixes_inline_and_refs_in_order():
    cfg = FieldDefinitionsConfig(definitions={
        "phone": FieldDefinition(key="phone", type="phone", label="Phone"),
    })
    step = WorkflowStepConfig(key="s", type="form", fields=[
        FieldConfig(name="note", type="text"),
        FieldRef(ref="phone", required=True),
    ])
    resolved = await resolve_step_fields(step, cfg=cfg)
    assert [f.name for f in resolved] == ["note", "phone"]
    assert resolved[1].type == "phone"
    assert resolved[1].required is True


async def test_resolve_step_fields_drops_dangling_ref(caplog):
    cfg = FieldDefinitionsConfig(definitions={})
    step = WorkflowStepConfig(key="s", type="form", fields=[
        FieldConfig(name="note", type="text"),
        FieldRef(ref="gone"),
    ])
    with caplog.at_level(logging.WARNING):
        resolved = await resolve_step_fields(step, cfg=cfg)
    assert [f.name for f in resolved] == ["note"]
    assert "gone" in caplog.text


async def test_save_then_delete_unused_definition():
    await save_field_definition(FieldDefinition(key="phone", type="phone"))
    cfg = await field_definitions_config.get()
    assert "phone" in cfg.definitions
    await delete_field_definition("phone")
    cfg = await field_definitions_config.get()
    assert "phone" not in cfg.definitions


async def test_definition_usages_dedupes_repeated_ref_in_step():
    await save_field_definition(FieldDefinition(key="phone", type="phone"))
    await workflows_config.set(WorkflowsConfig(workflows={
        "wf": WorkflowConfig(label="WF", steps=[
            WorkflowStepConfig(key="s", type="form",
                               fields=[FieldRef(ref="phone"), FieldRef(ref="phone")]),
        ]),
    }))
    assert await definition_usages("phone") == ["wf/s"]


async def test_filter_step_data_allows_resolved_ref_name_rejects_injection():
    await save_field_definition(FieldDefinition(key="phone", type="phone"))
    step = WorkflowStepConfig(key="info", type="form", fields=[
        FieldConfig(name="note", type="text"),
        FieldRef(ref="phone"),
    ])
    out = await _filter_step_data(step, {"note": "hi", "phone": "+33...", "returning_user_id": "x"})
    assert out == {"note": "hi", "phone": "+33..."}


async def test_delete_in_use_definition_is_blocked():
    await save_field_definition(FieldDefinition(key="phone", type="phone"))
    await workflows_config.set(WorkflowsConfig(workflows={
        "onboard": WorkflowConfig(label="Onboard", steps=[
            WorkflowStepConfig(key="info", type="form", fields=[FieldRef(ref="phone")]),
        ]),
    }))
    with pytest.raises(FieldDefinitionInUse) as exc:
        await delete_field_definition("phone")
    assert "onboard/info" in exc.value.usages
    cfg = await field_definitions_config.get()
    assert "phone" in cfg.definitions   # not deleted


def _editor_with(working: WorkflowsConfig, defs: dict) -> WorkflowEditorDialog:
    ed = WorkflowEditorDialog(user=None, original=WorkflowsConfig(workflows={}))
    ed.working_copy = working
    ed._vocab_keys = []
    ed._field_defs = defs
    return ed


def test_add_field_ref_appends_fieldref():
    working = WorkflowsConfig(workflows={
        "wf": WorkflowConfig(label="WF", steps=[WorkflowStepConfig(key="s", type="form", fields=[])]),
    })
    ed = _editor_with(working, {"phone": FieldDefinition(key="phone", type="phone")})
    ed.add_field_ref("wf", "s", "phone")
    field = working.workflows["wf"].steps[0].fields[0]
    assert isinstance(field, FieldRef) and field.ref == "phone"


def test_set_field_ref_override_and_clear():
    working = WorkflowsConfig(workflows={
        "wf": WorkflowConfig(label="WF", steps=[
            WorkflowStepConfig(key="s", type="form", fields=[FieldRef(ref="phone")]),
        ]),
    })
    ed = _editor_with(working, {"phone": FieldDefinition(key="phone", type="phone")})
    ed.set_field_ref_override("wf", "s", 0, "required", True)
    assert working.workflows["wf"].steps[0].fields[0].required is True
    ed.set_field_ref_override("wf", "s", 0, "required", None)
    assert working.workflows["wf"].steps[0].fields[0].required is None


def test_compute_warnings_flags_dangling_ref():
    working = WorkflowsConfig(workflows={
        "wf": WorkflowConfig(label="WF", steps=[
            WorkflowStepConfig(key="s", type="form", fields=[FieldRef(ref="gone")]),
        ]),
    })
    ed = _editor_with(working, {})
    warnings = ed.compute_warnings()
    assert any("gone" in w and "field definition" in w for w in warnings)


def test_set_field_label_with_autoslug_does_not_crash_with_fieldref():
    """Bug: set_field_label_with_autoslug iterated step.fields accessing f.name,
    but FieldRef has no .name attribute → AttributeError.

    Fix: use _resolved_step_fields(step) which returns only FieldConfig objects.
    """
    working = WorkflowsConfig(workflows={
        "wf": WorkflowConfig(label="WF", steps=[
            WorkflowStepConfig(key="s", type="form", fields=[
                FieldRef(ref="phone"),
                FieldConfig(name="", type="text"),
            ]),
        ]),
    })
    ed = _editor_with(working, {"phone": FieldDefinition(key="phone", type="phone")})
    # Before fix: raises AttributeError (FieldRef has no .name attribute)
    # After fix: succeeds and generates a slug from the label
    ed.set_field_label_with_autoslug("wf", "s", 1, "Some Label")
    field = working.workflows["wf"].steps[0].fields[1]
    assert field.label == "Some Label"
    assert field.name  # a slug was generated
