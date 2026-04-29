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
