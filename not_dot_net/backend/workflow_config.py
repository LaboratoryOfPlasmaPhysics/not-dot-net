"""The `workflows` config section.

Lives apart from `workflow_service` so that modules needing only the workflow
*definitions* (field_definitions, verification) can import them at module level:
importing them from the service created a cycle that both sides worked around
with function-local imports.
"""

from pydantic import BaseModel, Field, model_validator

from not_dot_net.backend.app_config import section
from not_dot_net.backend.default_workflows import default_workflows
from not_dot_net.config import TenureHookConfig, WorkflowConfig


class WorkflowsConfig(BaseModel):
    token_expiry_days: int = 30
    verification_code_expiry_minutes: int = 15
    max_upload_size_mb: int = 10
    workflows: dict[str, WorkflowConfig] = Field(default_factory=default_workflows)

    @model_validator(mode="before")
    @classmethod
    def _backfill_legacy_tenure_hook(cls, data):
        """Give a pre-hook saved onboarding config its tenure hook back.

        Configs stored before `WorkflowConfig.tenure` existed carry no `tenure`
        key, which would parse as None and silently stop recording tenures on
        upgrade. Only the absent case is filled: the editor always writes the
        key (`model_dump` emits None), so an admin who deliberately clears the
        hook keeps it cleared.
        """
        if not isinstance(data, dict):
            return data
        workflows = data.get("workflows")
        if not isinstance(workflows, dict):
            return data
        legacy = workflows.get("onboarding")
        if not isinstance(legacy, dict) or "tenure" in legacy:
            return data
        # Copy rather than mutate — `get()` hands out the cached raw JSON.
        return {
            **data,
            "workflows": {
                **workflows,
                "onboarding": {**legacy, "tenure": TenureHookConfig().model_dump(mode="json")},
            },
        }


workflows_config = section("workflows", WorkflowsConfig, label="Workflows")
