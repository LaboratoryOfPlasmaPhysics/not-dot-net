"""The `workflows` config section.

Lives apart from `workflow_service` so that modules needing only the workflow
*definitions* (field_definitions, verification) can import them at module level:
importing them from the service created a cycle that both sides worked around
with function-local imports.
"""

from pydantic import BaseModel, Field

from not_dot_net.backend.app_config import section
from not_dot_net.backend.default_workflows import default_workflows
from not_dot_net.config import WorkflowConfig


class WorkflowsConfig(BaseModel):
    token_expiry_days: int = 30
    verification_code_expiry_minutes: int = 15
    max_upload_size_mb: int = 10
    workflows: dict[str, WorkflowConfig] = Field(default_factory=default_workflows)


workflows_config = section("workflows", WorkflowsConfig, label="Workflows")
