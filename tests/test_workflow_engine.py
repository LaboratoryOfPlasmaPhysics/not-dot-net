import pytest
from not_dot_net.backend.workflow_engine import (
    get_current_step_config,
    get_available_actions,
    compute_next_step,
    can_user_act,
    get_completion_status,
)
from not_dot_net.config import WorkflowConfig, WorkflowStepConfig, FieldConfig


# --- Fixtures: minimal workflow configs ---

TWO_STEP_WORKFLOW = WorkflowConfig(
    label="Test",
    start_role="staff",
    steps=[
        WorkflowStepConfig(key="form1", type="form", assignee_role="staff", assignee_permission="create_workflows", actions=["submit"]),
        WorkflowStepConfig(key="approve", type="approval", assignee_role="director", assignee_permission="approve_workflows", actions=["approve", "reject"]),
    ],
)

PARTIAL_SAVE_WORKFLOW = WorkflowConfig(
    label="Test Partial",
    start_role="staff",
    steps=[
        WorkflowStepConfig(
            key="info",
            type="form",
            assignee="target_person",
            partial_save=True,
            fields=[
                FieldConfig(name="phone", type="text", required=True),
                FieldConfig(name="doc", type="file", required=True),
                FieldConfig(name="note", type="textarea", required=False),
            ],
            actions=["submit"],
        ),
    ],
)


class FakeRequest:
    def __init__(self, current_step, status="in_progress", data=None, target_email=None, created_by=None):
        self.current_step = current_step
        self.status = status
        self.data = data or {}
        self.target_email = target_email
        self.created_by = created_by


class FakeUser:
    def __init__(self, role, email="user@test.com", id="user-1"):
        self.role = role
        self.email = email
        self.id = id


# --- Tests ---

def test_get_current_step_config():
    req = FakeRequest(current_step="approve")
    step = get_current_step_config(req, TWO_STEP_WORKFLOW)
    assert step.key == "approve"
    assert step.type == "approval"


def test_get_current_step_config_invalid():
    req = FakeRequest(current_step="nonexistent")
    assert get_current_step_config(req, TWO_STEP_WORKFLOW) is None


def test_get_available_actions_form():
    req = FakeRequest(current_step="form1")
    actions = get_available_actions(req, TWO_STEP_WORKFLOW)
    assert actions == ["submit"]


def test_get_available_actions_approval():
    req = FakeRequest(current_step="approve")
    actions = get_available_actions(req, TWO_STEP_WORKFLOW)
    assert set(actions) == {"approve", "reject"}


def test_get_available_actions_completed_request():
    req = FakeRequest(current_step="approve", status="completed")
    actions = get_available_actions(req, TWO_STEP_WORKFLOW)
    assert actions == []


def test_compute_next_step_submit_advances():
    result = compute_next_step(TWO_STEP_WORKFLOW, "form1", "submit")
    assert result == ("approve", "in_progress")


def test_compute_next_step_approve_last_completes():
    result = compute_next_step(TWO_STEP_WORKFLOW, "approve", "approve")
    assert result == (None, "completed")


def test_compute_next_step_reject_terminates():
    result = compute_next_step(TWO_STEP_WORKFLOW, "approve", "reject")
    assert result == (None, "rejected")


def test_can_user_act_permission_based_step():
    """Steps with assignee_permission always return True (service layer checks permissions)."""
    user = FakeUser("member")
    req = FakeRequest(current_step="form1")
    assert can_user_act(user, req, TWO_STEP_WORKFLOW)


def test_can_user_act_permission_based_approval_step():
    """Approval steps with assignee_permission also return True from engine."""
    user = FakeUser("staff")
    req = FakeRequest(current_step="approve")
    assert can_user_act(user, req, TWO_STEP_WORKFLOW)


def test_can_user_act_target_person():
    user = FakeUser("member", email="target@test.com")
    req = FakeRequest(current_step="info", target_email="target@test.com")
    assert can_user_act(user, req, PARTIAL_SAVE_WORKFLOW)


def test_can_user_act_wrong_target():
    user = FakeUser("member", email="other@test.com")
    req = FakeRequest(current_step="info", target_email="target@test.com")
    assert not can_user_act(user, req, PARTIAL_SAVE_WORKFLOW)


def test_can_user_act_requester():
    requester_wf = WorkflowConfig(
        label="Test",
        start_role="staff",
        steps=[WorkflowStepConfig(key="review", type="form", assignee="requester", actions=["submit"])],
    )
    user = FakeUser("member", id="user-42")
    req = FakeRequest(current_step="review", created_by="user-42")
    assert can_user_act(user, req, requester_wf)
    other = FakeUser("member", id="user-99")
    assert not can_user_act(other, req, requester_wf)


def test_get_available_actions_partial_save_includes_save_draft():
    req = FakeRequest(current_step="info")
    actions = get_available_actions(req, PARTIAL_SAVE_WORKFLOW)
    assert "save_draft" in actions
    assert "submit" in actions


def test_completion_status_all_missing():
    req = FakeRequest(current_step="info", data={})
    step = PARTIAL_SAVE_WORKFLOW.steps[0]
    status = get_completion_status(req, step, files={})
    assert status["phone"] is False
    assert status["doc"] is False
    assert "note" not in status  # optional fields not tracked


def test_completion_status_partial():
    req = FakeRequest(current_step="info", data={"phone": "+33 1 23"})
    step = PARTIAL_SAVE_WORKFLOW.steps[0]
    status = get_completion_status(req, step, files={})
    assert status["phone"] is True
    assert status["doc"] is False


def test_completion_status_complete():
    req = FakeRequest(current_step="info", data={"phone": "+33 1 23"})
    step = PARTIAL_SAVE_WORKFLOW.steps[0]
    status = get_completion_status(req, step, files={"doc": True})
    assert status["phone"] is True
    assert status["doc"] is True


def test_compute_next_step_unknown_step_raises():
    with pytest.raises(ValueError, match="Unknown step"):
        compute_next_step(TWO_STEP_WORKFLOW, "nonexistent_step", "submit")
