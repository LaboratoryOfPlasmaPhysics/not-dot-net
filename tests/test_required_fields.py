"""Required fields are marked, and missing ones are highlighted on submit.

Review 2026-10-12 V4: only file fields carried a `*`, and a failed submit
listed missing fields in one toast without pointing at the inputs.
"""

from nicegui import ui
from nicegui.testing import User

from not_dot_net.config import FieldConfig, WorkflowStepConfig
from not_dot_net.frontend.workflow_step import render_step_form

STEP = WorkflowStepConfig(key="s1", type="form", actions=["submit"], fields=[
    FieldConfig(name="first_name", type="text", label="First name", required=True),
    FieldConfig(name="nickname", type="text", label="Nickname"),
])


async def _open(user: User, path: str, submitted: list) -> dict:
    rendered = {}

    async def on_submit(data):
        submitted.append(data)

    @ui.page(path)
    async def _page():
        rendered.update(await render_step_form(STEP, data={}, on_submit=on_submit))

    await user.open(path)
    return rendered


async def test_required_field_label_is_marked(user: User):
    await _open(user, "/_req_marked", [])
    await user.should_see("First name *")
    await user.should_not_see("Nickname *")


async def test_missing_required_field_is_highlighted_then_cleared(user: User):
    submitted = []
    fields = await _open(user, "/_req_highlight", submitted)

    user.find("Submit").click()
    await user.should_see("This field is required")
    assert fields["first_name"].props.get("error") is True
    assert not fields["nickname"].props.get("error")
    assert submitted == []

    fields["first_name"].value = "Marie"
    assert not fields["first_name"].props.get("error")
