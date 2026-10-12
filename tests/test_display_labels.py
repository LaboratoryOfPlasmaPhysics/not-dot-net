"""Users see labels, never internal codes.

Review 2026-10-12 V2/V3: the dashboard showed raw statuses (`in_progress`)
and the timeline raw `step_key: action` (`admin_validation: approve`).
"""

from not_dot_net.config import WorkflowConfig, WorkflowStepConfig
from not_dot_net.frontend.workflow_step import event_label

WF = WorkflowConfig(label="Onboarding", steps=[
    WorkflowStepConfig(key="admin_validation", type="approval", label="Admin validation"),
    WorkflowStepConfig(key="it_account_creation", type="form"),
])


def test_event_label_uses_step_label_and_past_tense_action():
    assert event_label(WF, "admin_validation", "approve") == "Admin validation: approved"


def test_event_label_prettifies_unlabelled_step():
    assert event_label(WF, "it_account_creation", "complete") == "It account creation: completed"


def test_event_label_survives_unknown_step_and_action():
    """A step deleted from the config, or an action from a newer version."""
    assert event_label(WF, "gone_step", "teleport") == "gone_step: teleport"


def test_every_recorded_action_has_a_label():
    from not_dot_net.frontend.i18n import TRANSLATIONS
    actions = {"create", "submit", "approve", "reject", "request_corrections",
               "complete", "cancel", "save_draft"}
    for locale in ("en", "fr"):
        assert {f"did_{a}" for a in actions} <= set(TRANSLATIONS[locale]), locale
