"""Pure-function workflow step machine. No DB, no side effects."""

from not_dot_net.backend.workflow_models import RequestStatus
from not_dot_net.config import WorkflowConfig, WorkflowStepConfig


def get_current_step_config(request, workflow: WorkflowConfig) -> WorkflowStepConfig | None:
    """Get the step config for the request's current step."""
    for step in workflow.steps:
        if step.key == request.current_step:
            return step
    return None


def get_step_progress(request, workflow: WorkflowConfig) -> tuple[int, int]:
    """Return (current_step_1based, total_steps) for progress display."""
    total = len(workflow.steps)
    if request.status == RequestStatus.COMPLETED:
        return (total, total)
    if request.status == RequestStatus.REJECTED:
        step_keys = [s.key for s in workflow.steps]
        idx = step_keys.index(request.current_step) if request.current_step in step_keys else 0
        return (idx + 1, total)
    step_keys = [s.key for s in workflow.steps]
    if request.current_step in step_keys:
        return (step_keys.index(request.current_step) + 1, total)
    return (0, total)


def get_available_actions(request, workflow: WorkflowConfig) -> list[str]:
    """Get actions available for the current step. Empty if request is terminal."""
    if request.status in (RequestStatus.COMPLETED, RequestStatus.REJECTED, RequestStatus.CANCELLED):
        return []
    step = get_current_step_config(request, workflow)
    if step is None:
        return []
    actions = list(step.actions)
    if step.partial_save and "save_draft" not in actions:
        actions.append("save_draft")
    return actions


def compute_next_step(
    workflow: WorkflowConfig, current_step_key: str, action: str
) -> tuple[str | None, str]:
    """Given an action, return (next_step_key, new_status)."""
    if action == "reject":
        return (None, RequestStatus.REJECTED)
    if action == "save_draft":
        return (current_step_key, RequestStatus.IN_PROGRESS)
    step_keys = [s.key for s in workflow.steps]
    if current_step_key not in step_keys:
        raise ValueError(f"Unknown step '{current_step_key}' in workflow")
    idx = step_keys.index(current_step_key)
    if idx + 1 < len(step_keys):
        return (step_keys[idx + 1], RequestStatus.IN_PROGRESS)
    return (None, RequestStatus.COMPLETED)


def can_user_act(user, request, workflow: WorkflowConfig) -> bool:
    """Check if a user can act on the current step (contextual assignment only).

    Permission-based checks are handled by the service layer.
    """
    step = get_current_step_config(request, workflow)
    if step is None:
        return False

    # Contextual assignment
    if step.assignee == "target_person":
        return user.email == request.target_email
    if step.assignee == "requester":
        return str(user.id) == str(request.created_by)

    # If step has assignee_permission or assignee_role, the service layer handles it
    return step.assignee_permission is not None or step.assignee_role is not None


def get_completion_status(
    request, step: WorkflowStepConfig, files: dict[str, bool]
) -> dict[str, bool]:
    """For a form step, return {field_name: is_filled} for required fields only."""
    status = {}
    for field in step.fields:
        if not field.required:
            continue
        if field.type == "file":
            status[field.name] = files.get(field.name, False)
        else:
            value = request.data.get(field.name)
            status[field.name] = bool(value)
    return status
