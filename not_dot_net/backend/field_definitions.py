"""App-wide reusable field definitions — Vocab registry Phase 2.

A FieldDefinition describes a workflow field once (type, label, required,
vocabulary binding, encrypted, layout). Workflow steps reference it by key
(via config.FieldRef) and resolve it live. Stored in one app_setting JSON
row, the ConfigSection idiom — no table, no migration.
"""

import logging

from pydantic import BaseModel, Field

from not_dot_net.backend.app_config import section
from not_dot_net.backend.workflow_config import workflows_config
from not_dot_net.config import FieldConfig, FieldRef, WorkflowStepConfig, resolve_field_ref

_log = logging.getLogger(__name__)


class FieldDefinition(BaseModel):
    key: str                       # immutable registry key; also the resolved field's data name
    type: str                      # text | email | textarea | date | select | file | phone | location | checkbox
    label: str = ""
    required: bool = False
    options_key: str | None = None # vocabulary binding (select), resolved via the Phase-1 registry
    encrypted: bool = False
    half_width: bool = False


class FieldDefinitionsConfig(BaseModel):
    definitions: dict[str, FieldDefinition] = Field(default_factory=dict)


field_definitions_config = section("field_definitions", FieldDefinitionsConfig,
                                   label="Field definitions")


async def resolve_step_fields(
    step: WorkflowStepConfig, *, cfg: FieldDefinitionsConfig | None = None
) -> list[FieldConfig]:
    """Flatten a step's fields: inline fields pass through; references resolve
    against their definition. A reference whose definition is missing is dropped
    (deletion is normally blocked; this guards hand-edited/imported configs)."""
    if cfg is None:
        cfg = await field_definitions_config.get()
    resolved: list[FieldConfig] = []
    for item in step.fields:
        if isinstance(item, FieldRef):
            defn = cfg.definitions.get(item.ref)
            if defn is None:
                _log.warning("step %r references unknown field definition %r — dropped",
                             step.key, item.ref)
                continue
            resolved.append(resolve_field_ref(item, defn))
        else:
            resolved.append(item)
    return resolved


class FieldDefinitionInUse(Exception):
    def __init__(self, key: str, usages: list[str]):
        self.key = key
        self.usages = usages
        super().__init__(f"field definition '{key}' is used by: {', '.join(usages)}")


async def save_field_definition(defn: FieldDefinition) -> None:
    cfg = await field_definitions_config.get()
    cfg.definitions[defn.key] = defn
    await field_definitions_config.set(cfg)


async def definition_usages(key: str) -> list[str]:
    wf_cfg = await workflows_config.get()
    usages: list[str] = []
    for wf_key, wf in wf_cfg.workflows.items():
        for step in wf.steps:
            if any(isinstance(item, FieldRef) and item.ref == key for item in step.fields):
                usages.append(f"{wf_key}/{step.key}")
    return usages


async def delete_field_definition(key: str) -> None:
    usages = await definition_usages(key)
    if usages:
        raise FieldDefinitionInUse(key, usages)
    cfg = await field_definitions_config.get()
    cfg.definitions.pop(key, None)
    await field_definitions_config.set(cfg)
