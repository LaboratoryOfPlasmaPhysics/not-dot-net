"""Pure-function workflow step machine. Mostly pure — can_user_act is async
because it needs to check DB-backed permissions."""

from not_dot_net.backend.workflow_models import RequestStatus
from not_dot_net.config import WorkflowConfig, WorkflowStepConfig


def _email_eq(a: str | None, b: str | None) -> bool:
    """Case-insensitive email comparison.

    AD often returns mixed-case `mail`; users may type `Alice@LPP.fr`.
    """
    if not a or not b:
        return False
    return a.strip().lower() == b.strip().lower()


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
    if action == "request_corrections":
        step = next((s for s in workflow.steps if s.key == current_step_key), None)
        if step is None or not step.corrections_target:
            raise ValueError(
                f"Step '{current_step_key}' has no corrections_target configured"
            )
        return (step.corrections_target, RequestStatus.IN_PROGRESS)
    step_keys = [s.key for s in workflow.steps]
    if current_step_key not in step_keys:
        raise ValueError(f"Unknown step '{current_step_key}' in workflow")
    idx = step_keys.index(current_step_key)
    if idx + 1 < len(step_keys):
        return (step_keys[idx + 1], RequestStatus.IN_PROGRESS)
    return (None, RequestStatus.COMPLETED)


async def can_user_act(user, request, workflow: WorkflowConfig) -> bool:
    """Check if a user can act on the current step."""
    from not_dot_net.backend.permissions import has_permissions

    step = get_current_step_config(request, workflow)
    if step is None:
        return False

    if step.assignee == "target_person":
        return _email_eq(user.email, request.target_email)
    if step.assignee == "requester":
        return str(user.id) == str(request.created_by)
    if step.assignee_permission:
        return await has_permissions(user, step.assignee_permission)
    if step.assignee_role:
        return user.role == step.assignee_role
    return False


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
