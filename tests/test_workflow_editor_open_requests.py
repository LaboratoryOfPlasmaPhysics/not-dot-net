"""The workflow editor must not strand requests that are in progress.

Review 2026-10-12 W5: deleting or renaming a step (or the whole workflow)
that open requests sit on left them failing with "Unknown step", dropped from
every actionable list. Renaming a step also left `corrections_target` and
notification rules pointing at the old key.
"""

from nicegui import ui
from nicegui.testing import User

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_config import WorkflowsConfig, workflows_config
from not_dot_net.backend.workflow_models import WorkflowRequest
from not_dot_net.backend.workflow_service import stranded_requests
from not_dot_net.config import NotificationRuleConfig, WorkflowConfig, WorkflowStepConfig
from tests.test_workflow_editor import admin_user  # noqa: F401  (fixture)

CONFIG = WorkflowsConfig(workflows={
    "a": WorkflowConfig(label="A", steps=[
        WorkflowStepConfig(key="fill", type="form"),
        WorkflowStepConfig(key="review", type="approval", corrections_target="fill"),
    ], notifications=[NotificationRuleConfig(event="submit", step="fill", notify=["requester"])]),
})


async def _request_on(step: str, status: str = "in_progress") -> None:
    async with session_scope() as session:
        session.add(WorkflowRequest(type="a", current_step=step, status=status))
        await session.commit()


async def test_stranded_requests_lists_open_requests_on_missing_steps():
    await _request_on("review")
    await _request_on("fill", status="completed")
    without_review = CONFIG.model_copy(deep=True)
    without_review.workflows["a"].steps = without_review.workflows["a"].steps[:1]

    assert [(r.type, r.current_step) for r in await stranded_requests(without_review)] == [("a", "review")]
    assert await stranded_requests(CONFIG) == []
    assert len(await stranded_requests(WorkflowsConfig(workflows={}))) == 1


async def _editor(user: User, admin, path: str):
    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog
    await workflows_config.set(CONFIG)
    captured = {}

    @ui.page(path)
    async def _page():
        captured["dlg"] = await WorkflowEditorDialog.create(admin)

    await user.open(path)
    return captured["dlg"]


async def test_save_refused_when_it_would_strand_open_requests(user: User, admin_user):  # noqa: F811
    await _request_on("review")
    dlg = await _editor(user, admin_user, "/_w5_save")
    dlg.delete_step("a", "review")
    await dlg.save()

    await user.should_see("in progress")
    assert [s.key for s in (await workflows_config.get()).workflows["a"].steps] == ["fill", "review"]


async def test_step_rename_rewrites_references(user: User, admin_user):  # noqa: F811
    dlg = await _editor(user, admin_user, "/_w5_rename")
    dlg.select("a", "fill")
    dlg.set_step_field("a", "fill", "key", "details")

    wf = dlg.working_copy.workflows["a"]
    assert wf.steps[1].corrections_target == "details"
    assert wf.notifications[0].step == "details"
