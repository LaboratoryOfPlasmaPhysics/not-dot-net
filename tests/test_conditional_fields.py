"""Pure-predicate tests for FieldConfig.visible_when — no NiceGUI."""

from not_dot_net.config import FieldConfig, is_field_visible


def test_no_rule_means_always_visible():
    f = FieldConfig(name="x", type="text")
    assert is_field_visible(f, {}) is True
    assert is_field_visible(f, {"anything": True}) is True


def test_rule_matches_value():
    f = FieldConfig(name="zrr_topic", type="text", visible_when={"zrr": True})
    assert is_field_visible(f, {"zrr": True}) is True
    assert is_field_visible(f, {"zrr": False}) is False
    assert is_field_visible(f, {}) is False  # missing key counts as mismatch


def test_rule_with_string_value():
    f = FieldConfig(name="cdd_end", type="date", visible_when={"status": "CDD"})
    assert is_field_visible(f, {"status": "CDD"}) is True
    assert is_field_visible(f, {"status": "CDI"}) is False


def test_rule_with_multiple_keys_is_and():
    """Future-proof: if more than one key/value pair, all must match."""
    f = FieldConfig(name="x", type="text", visible_when={"a": True, "b": "y"})
    assert is_field_visible(f, {"a": True, "b": "y"}) is True
    assert is_field_visible(f, {"a": True, "b": "z"}) is False
    assert is_field_visible(f, {"a": False, "b": "y"}) is False


def test_required_skip_uses_same_predicate():
    """Demonstrate the contract: if is_field_visible is False, callers can
    treat the field as 'effectively not required'. (The render and submit
    paths both use this exact rule.)"""
    f = FieldConfig(name="zrr_topic", type="text", required=True,
                    visible_when={"zrr": True})

    # Required + hidden → skip (predicate returns False)
    assert is_field_visible(f, {"zrr": False}) is False

    # Required + visible + empty → caller will flag as missing
    assert is_field_visible(f, {"zrr": True}) is True


import pytest
from nicegui import ui
from nicegui.testing import User


async def test_form_hides_field_when_checkbox_false(user: User):
    """Rendering a step with a visible_when text field: the dependent field
    is hidden when the checkbox is False, visible when True."""
    from not_dot_net.frontend.workflow_step import render_step_form
    from not_dot_net.config import WorkflowStepConfig, FieldConfig

    step = WorkflowStepConfig(
        key="s1", type="form",
        fields=[
            FieldConfig(name="zrr", type="checkbox", label="ZRR"),
            FieldConfig(name="zrr_topic", type="text", label="Topic",
                        visible_when={"zrr": True}),
        ],
        actions=["submit"],
    )

    async def on_submit(data):
        pass

    @ui.page("/_visibility_off")
    async def _page():
        await render_step_form(step, data={"zrr": False}, on_submit=on_submit)

    await user.open("/_visibility_off")
    await user.should_not_see("Topic")


async def test_form_shows_field_when_checkbox_true(user: User):
    from not_dot_net.frontend.workflow_step import render_step_form
    from not_dot_net.config import WorkflowStepConfig, FieldConfig

    step = WorkflowStepConfig(
        key="s1", type="form",
        fields=[
            FieldConfig(name="zrr", type="checkbox", label="ZRR"),
            FieldConfig(name="zrr_topic", type="text", label="Topic",
                        visible_when={"zrr": True}),
        ],
        actions=["submit"],
    )

    async def on_submit(data):
        pass

    @ui.page("/_visibility_on")
    async def _page():
        await render_step_form(step, data={"zrr": True}, on_submit=on_submit)

    await user.open("/_visibility_on")
    await user.should_see("Topic")


async def test_field_shown_when_checkbox_unchecked_is_visible_on_fresh_form(user: User):
    """Review 2026-10-12 W4: with no saved value, the render checked None == False
    (hidden) while submit checked the unchecked box's False (visible, required):
    "required field: Reason" for a field the user never saw."""
    from not_dot_net.frontend.workflow_step import render_step_form
    from not_dot_net.config import WorkflowStepConfig, FieldConfig

    step = WorkflowStepConfig(
        key="s1", type="form",
        fields=[
            FieldConfig(name="has_badge", type="checkbox", label="Has badge"),
            FieldConfig(name="no_badge_reason", type="text", label="Reason",
                        required=True, visible_when={"has_badge": False}),
        ],
        actions=["submit"],
    )

    async def on_submit(data):
        pass

    @ui.page("/_visibility_unchecked_default")
    async def _page():
        await render_step_form(step, data={}, on_submit=on_submit)

    await user.open("/_visibility_unchecked_default")
    await user.should_see("Reason")
