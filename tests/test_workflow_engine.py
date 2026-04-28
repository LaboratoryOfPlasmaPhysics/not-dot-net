import pytest
from not_dot_net.backend.workflow_engine import (
    get_current_step_config,
    get_step_progress,
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


@pytest.mark.parametrize("status", ["rejected", "cancelled"])
def test_get_available_actions_terminal_requests(status: str):
    req = FakeRequest(current_step="approve", status=status)
    assert get_available_actions(req, TWO_STEP_WORKFLOW) == []


def test_get_available_actions_unknown_step_returns_empty():
    req = FakeRequest(current_step="missing")
    assert get_available_actions(req, TWO_STEP_WORKFLOW) == []


def test_compute_next_step_submit_advances():
    result = compute_next_step(TWO_STEP_WORKFLOW, "form1", "submit")
    assert result == ("approve", "in_progress")


def test_compute_next_step_save_draft_stays_on_current_step():
    result = compute_next_step(TWO_STEP_WORKFLOW, "form1", "save_draft")
    assert result == ("form1", "in_progress")


def test_compute_next_step_approve_last_completes():
    result = compute_next_step(TWO_STEP_WORKFLOW, "approve", "approve")
    assert result == (None, "completed")


def test_compute_next_step_reject_terminates():
    result = compute_next_step(TWO_STEP_WORKFLOW, "approve", "reject")
    assert result == (None, "rejected")


def test_compute_next_step_custom_action_advances_like_submit():
    result = compute_next_step(TWO_STEP_WORKFLOW, "form1", "complete")
    assert result == ("approve", "in_progress")


def test_get_step_progress_in_progress():
    req = FakeRequest(current_step="approve")
    assert get_step_progress(req, TWO_STEP_WORKFLOW) == (2, 2)


def test_get_step_progress_completed_returns_total():
    req = FakeRequest(current_step="approve", status="completed")
    assert get_step_progress(req, TWO_STEP_WORKFLOW) == (2, 2)


def test_get_step_progress_rejected_stays_on_current_step():
    req = FakeRequest(current_step="form1", status="rejected")
    assert get_step_progress(req, TWO_STEP_WORKFLOW) == (1, 2)


def test_get_step_progress_unknown_step_returns_zero():
    req = FakeRequest(current_step="missing")
    assert get_step_progress(req, TWO_STEP_WORKFLOW) == (0, 2)


async def _setup_roles():
    from not_dot_net.backend.roles import roles_config, RolesConfig, RoleDefinition
    await roles_config.set(RolesConfig(roles={
        "staff": RoleDefinition(label="Staff", permissions=["create_workflows"]),
        "director": RoleDefinition(label="Director", permissions=["approve_workflows"]),
        "member": RoleDefinition(label="Member", permissions=[]),
        "admin": RoleDefinition(label="Admin", permissions=["create_workflows", "approve_workflows", "manage_roles", "manage_settings"]),
    }))


async def test_can_user_act_permission_granted():
    """User with the right permission can act on a permission-gated step."""
    await _setup_roles()
    user = FakeUser("staff")
    req = FakeRequest(current_step="form1")
    assert await can_user_act(user, req, TWO_STEP_WORKFLOW)


async def test_can_user_act_permission_denied():
    """User without the right permission cannot act on a permission-gated step."""
    await _setup_roles()
    user = FakeUser("member")
    req = FakeRequest(current_step="form1")
    assert not await can_user_act(user, req, TWO_STEP_WORKFLOW)


async def test_can_user_act_approval_permission_granted():
    """Director with approve_workflows can act on approval step."""
    await _setup_roles()
    user = FakeUser("director")
    req = FakeRequest(current_step="approve")
    assert await can_user_act(user, req, TWO_STEP_WORKFLOW)


async def test_can_user_act_approval_permission_denied():
    """Staff without approve_workflows cannot act on approval step."""
    await _setup_roles()
    user = FakeUser("staff")
    req = FakeRequest(current_step="approve")
    assert not await can_user_act(user, req, TWO_STEP_WORKFLOW)


async def test_can_user_act_target_person():
    user = FakeUser("member", email="target@test.com")
    req = FakeRequest(current_step="info", target_email="target@test.com")
    assert await can_user_act(user, req, PARTIAL_SAVE_WORKFLOW)


async def test_can_user_act_wrong_target():
    user = FakeUser("member", email="other@test.com")
    req = FakeRequest(current_step="info", target_email="target@test.com")
    assert not await can_user_act(user, req, PARTIAL_SAVE_WORKFLOW)


async def test_can_user_act_requester():
    requester_wf = WorkflowConfig(
        label="Test",
        start_role="staff",
        steps=[WorkflowStepConfig(key="review", type="form", assignee="requester", actions=["submit"])],
    )
    user = FakeUser("member", id="user-42")
    req = FakeRequest(current_step="review", created_by="user-42")
    assert await can_user_act(user, req, requester_wf)
    other = FakeUser("member", id="user-99")
    assert not await can_user_act(other, req, requester_wf)


async def test_can_user_act_permission_takes_precedence_over_role():
    await _setup_roles()
    wf = WorkflowConfig(
        label="Permission First",
        steps=[
            WorkflowStepConfig(
                key="review",
                type="approval",
                assignee_role="director",
                assignee_permission="approve_workflows",
                actions=["approve"],
            ),
        ],
    )
    req = FakeRequest(current_step="review")

    assert await can_user_act(FakeUser("director"), req, wf)
    assert not await can_user_act(FakeUser("staff"), req, wf)


async def test_can_user_act_role_only_step():
    wf = WorkflowConfig(
        label="Role Only",
        steps=[
            WorkflowStepConfig(
                key="review",
                type="approval",
                assignee_role="director",
                actions=["approve"],
            ),
        ],
    )
    req = FakeRequest(current_step="review")

    assert await can_user_act(FakeUser("director"), req, wf)
    assert not await can_user_act(FakeUser("staff"), req, wf)


async def test_can_user_act_unassigned_step_denied():
    wf = WorkflowConfig(
        label="Unassigned",
        steps=[WorkflowStepConfig(key="open", type="form", actions=["submit"])],
    )
    req = FakeRequest(current_step="open")

    assert not await can_user_act(FakeUser("admin"), req, wf)


async def test_can_user_act_unknown_step_denied():
    assert not await can_user_act(FakeUser("admin"), FakeRequest("missing"), TWO_STEP_WORKFLOW)


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


@pytest.mark.parametrize("empty_value", ["", None, 0, False])
def test_completion_status_treats_empty_required_values_as_missing(empty_value):
    req = FakeRequest(current_step="info", data={"phone": empty_value})
    step = PARTIAL_SAVE_WORKFLOW.steps[0]

    status = get_completion_status(req, step, files={"doc": True})

    assert status["phone"] is False
    assert status["doc"] is True


def test_compute_next_step_unknown_step_raises():
    with pytest.raises(ValueError, match="Unknown step"):
        compute_next_step(TWO_STEP_WORKFLOW, "nonexistent_step", "submit")


# --- Task 4: Config model changes tests ---

def test_field_config_encrypted_default_false():
    fc = FieldConfig(name="doc", type="file")
    assert fc.encrypted is False


def test_field_config_encrypted_true():
    fc = FieldConfig(name="doc", type="file", encrypted=True)
    assert fc.encrypted is True


def test_step_config_corrections_target():
    sc = WorkflowStepConfig(
        key="validation",
        type="approval",
        actions=["approve", "request_corrections", "reject"],
        corrections_target="newcomer_info",
    )
    assert sc.corrections_target == "newcomer_info"


def test_step_config_corrections_target_default_none():
    sc = WorkflowStepConfig(key="step", type="form")
    assert sc.corrections_target is None


def test_workflow_config_document_instructions():
    wc = WorkflowConfig(
        label="Test",
        steps=[],
        document_instructions={"Intern": ["ID document"], "_default": ["ID", "RIB"]},
    )
    assert wc.document_instructions["Intern"] == ["ID document"]
    assert wc.document_instructions["_default"] == ["ID", "RIB"]


def test_workflow_config_document_instructions_default_empty():
    wc = WorkflowConfig(label="Test", steps=[])
    assert wc.document_instructions == {}


# --- Task 5: Engine request_corrections action tests ---

def test_request_corrections_returns_target_step():
    wf = WorkflowConfig(
        label="Test",
        steps=[
            WorkflowStepConfig(key="form", type="form", actions=["submit"]),
            WorkflowStepConfig(
                key="validation", type="approval",
                actions=["approve", "request_corrections", "reject"],
                corrections_target="form",
            ),
        ],
    )
    next_step, status = compute_next_step(wf, "validation", "request_corrections")
    assert next_step == "form"
    assert status == "in_progress"


def test_request_corrections_without_target_raises():
    wf = WorkflowConfig(
        label="Test",
        steps=[
            WorkflowStepConfig(key="form", type="form", actions=["submit"]),
            WorkflowStepConfig(
                key="validation", type="approval",
                actions=["approve", "request_corrections", "reject"],
            ),
        ],
    )
    with pytest.raises(ValueError, match="corrections_target"):
        compute_next_step(wf, "validation", "request_corrections")


def test_request_corrections_target_is_not_validated_by_engine():
    wf = WorkflowConfig(
        label="Test",
        steps=[
            WorkflowStepConfig(
                key="validation", type="approval",
                actions=["request_corrections"],
                corrections_target="missing-step",
            ),
        ],
    )
    next_step, status = compute_next_step(wf, "validation", "request_corrections")
    assert next_step == "missing-step"
    assert status == "in_progress"
