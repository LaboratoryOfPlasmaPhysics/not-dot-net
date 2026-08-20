"""Integration tests for FieldRef resolution in render/validate/display paths."""

from types import SimpleNamespace

import pytest
from nicegui import ui
from nicegui.testing import User

from not_dot_net.backend.field_definitions import (
    FieldDefinition,
    FieldDefinitionsConfig,
    field_definitions_config,
    save_field_definition,
    delete_field_definition,
)
from not_dot_net.backend.vocabularies import (
    VocabulariesConfig,
    StoredVocabulary,
    VocabularyTerm,
    vocabularies_config,
)
from not_dot_net.backend.workflow_config import WorkflowsConfig, workflows_config
from not_dot_net.config import FieldConfig, FieldRef, WorkflowConfig, WorkflowStepConfig
from not_dot_net.frontend.field_definitions_editor import render as render_field_definitions
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.new_request import render
from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
from not_dot_net.frontend.workflow_step import resolve_display_values


@pytest.fixture
def superuser():
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        email="admin@test",
        is_superuser=True,
        is_active=True,
        role="",
    )


async def test_referenced_field_renders_with_definition_label(user: User, superuser):
    """A FieldRef in a step renders with the definition's label."""
    await field_definitions_config.set(FieldDefinitionsConfig(definitions={
        "phone": FieldDefinition(key="phone", type="phone", label="Phone number"),
    }))
    await workflows_config.set(WorkflowsConfig(workflows={
        "mission": WorkflowConfig(label="Mission", steps=[
            WorkflowStepConfig(key="info", type="form", assignee="requester", fields=[
                FieldRef(ref="phone"),
            ]),
        ]),
    }))

    @ui.page("/_fd_label_test")
    async def _page():
        await render(superuser)

    await user.open("/_fd_label_test")
    user.find(kind=ui.card).click()
    await user.should_see("Phone number")


async def test_referenced_field_required_prevents_empty_submit(user: User, superuser):
    """A FieldRef(required=True) makes the rendered field required — submitting empty shows notice."""
    await field_definitions_config.set(FieldDefinitionsConfig(definitions={
        "contact": FieldDefinition(key="contact", type="text", label="Contact"),
    }))
    await workflows_config.set(WorkflowsConfig(workflows={
        "req_test": WorkflowConfig(label="Req Test", steps=[
            WorkflowStepConfig(key="info", type="form", assignee="requester", fields=[
                FieldRef(ref="contact", required=True),
            ]),
        ]),
    }))

    @ui.page("/_fd_required_test")
    async def _page():
        await render(superuser)

    await user.open("/_fd_required_test")
    user.find(kind=ui.card).click()
    await user.should_see("Contact")
    # Submit without filling in — must see required-field notice
    user.find(t("submit")).click()
    await user.should_see(t("required_field"))


async def test_edit_definition_label_applies_everywhere(user: User, superuser):
    """Editing the definition label and re-rendering shows the new label."""
    await save_field_definition(FieldDefinition(key="job", type="text", label="Job title"))
    await workflows_config.set(WorkflowsConfig(workflows={
        "job_wf": WorkflowConfig(label="Job WF", steps=[
            WorkflowStepConfig(key="info", type="form", assignee="requester", fields=[
                FieldRef(ref="job"),
            ]),
        ]),
    }))

    @ui.page("/_fd_edit_label_before")
    async def _page_before():
        await render(superuser)

    await user.open("/_fd_edit_label_before")
    user.find(kind=ui.card).click()
    await user.should_see("Job title")

    # Now update the definition
    await save_field_definition(FieldDefinition(key="job", type="text", label="Position"))

    @ui.page("/_fd_edit_label_after")
    async def _page_after():
        await render(superuser)

    await user.open("/_fd_edit_label_after")
    user.find(kind=ui.card).click()
    await user.should_see("Position")


async def test_resolve_display_values_maps_referenced_select_code_to_label():
    """resolve_display_values resolves a select code from a FieldRef to its vocabulary label."""
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "nat": StoredVocabulary(key="nat", label={"en": "Nat"}, terms=[
            VocabularyTerm(code="FR", labels={"en": "French", "fr": "Français"}),
        ]),
    }))
    await field_definitions_config.set(FieldDefinitionsConfig(definitions={
        "nationality": FieldDefinition(key="nationality", type="select", label="Nationality", options_key="nat"),
    }))
    wf = WorkflowConfig(label="WF", steps=[
        WorkflowStepConfig(key="s", type="form", fields=[
            FieldRef(ref="nationality"),
        ]),
    ])
    out = await resolve_display_values(wf, {"nationality": "FR"}, "fr")
    assert out["nationality"] == "Français"


async def test_admin_can_create_field_definition(user: User, superuser) -> None:
    @ui.page("/_fd_create_test")
    async def _page():
        await render_field_definitions(superuser)

    await user.open("/_fd_create_test")
    await user.should_see(t("field_defs_new"))
    user.find(t("field_defs_new")).click()
    await user.should_see(t("field_defs_name"))
    user.find(t("field_defs_name")).type("Phone number")
    user.find(t("save")).click()
    await user.should_see(kind=ui.label, content="Phone number")

    cfg = await field_definitions_config.get()
    assert any(d.label == "Phone number" or k == "phone_number"
               for k, d in cfg.definitions.items())


async def test_delete_in_use_is_refused(user: User, superuser) -> None:
    await save_field_definition(FieldDefinition(key="phone", type="phone", label="Phone"))
    await workflows_config.set(WorkflowsConfig(workflows={
        "mission": WorkflowConfig(label="Mission", steps=[
            WorkflowStepConfig(key="info", type="form", assignee="requester", fields=[
                FieldRef(ref="phone"),
            ]),
        ]),
    }))

    @ui.page("/_fd_delete_test")
    async def _page():
        await render_field_definitions(superuser)

    await user.open("/_fd_delete_test")
    await user.should_see("Phone")
    user.find(kind=ui.button, content="delete").click()
    await user.should_see(t("cancel"))
    user.find(kind=ui.button, content=t("delete")).click()
    await user.should_see("mission/info")

    cfg = await field_definitions_config.get()
    assert "phone" in cfg.definitions


async def test_workflow_editor_renders_shared_field_badge(user: User, superuser):
    """A step containing a FieldRef renders the 'shared:' badge in the editor."""
    await save_field_definition(FieldDefinition(key="phone", type="phone", label="Phone"))
    await workflows_config.set(WorkflowsConfig(workflows={
        "wf": WorkflowConfig(label="WF", steps=[
            WorkflowStepConfig(key="info", type="form", fields=[FieldRef(ref="phone")]),
        ]),
    }))
    captured = {}

    @ui.page("/_we_ref_badge")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(superuser)

    await user.open("/_we_ref_badge")
    captured["dlg"].select("wf", "info")
    await user.should_see(t("field_shared_badge", ref="phone"))


async def test_workflow_editor_use_shared_button_and_append(user: User, superuser):
    """The 'Use shared field' button appears when definitions exist, and adding
    a shared field appends a FieldRef to the step."""
    await save_field_definition(FieldDefinition(key="phone", type="phone", label="Phone"))
    await workflows_config.set(WorkflowsConfig(workflows={
        "wf": WorkflowConfig(label="WF", steps=[
            WorkflowStepConfig(key="info", type="form", fields=[]),
        ]),
    }))
    captured = {}

    @ui.page("/_we_use_shared")
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(superuser)

    await user.open("/_we_use_shared")
    dlg = captured["dlg"]
    dlg.select("wf", "info")
    await user.should_see(t("field_use_shared"))
    # Clicking the button opens the picker dialog (proves the button wiring).
    # The confirm button itself closes its dialog inside the click handler, which
    # the NiceGUI test harness cannot drive (it mutates the listener dict mid-
    # iteration), so the append is asserted via add_field_ref directly.
    user.find(t("field_use_shared")).click()
    await user.should_see(t("field_definitions"))
    dlg.add_field_ref("wf", "info", "phone")
    fields = dlg.working_copy.workflows["wf"].steps[0].fields
    assert len(fields) == 1
    assert isinstance(fields[0], FieldRef)
    assert fields[0].ref == "phone"
