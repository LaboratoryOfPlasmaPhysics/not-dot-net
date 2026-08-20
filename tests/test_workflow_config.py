import pytest
from not_dot_net.config import WorkflowStepConfig, WorkflowConfig
from not_dot_net.backend.workflow_config import workflows_config


async def test_settings_has_workflows():
    cfg = await workflows_config.get()
    assert hasattr(cfg, "workflows")
    assert isinstance(cfg.workflows, dict)


async def test_default_workflows_include_onboarding_and_vpn():
    cfg = await workflows_config.get()
    assert "onboarding" in cfg.workflows
    assert "vpn_access" in cfg.workflows


async def test_workflow_config_has_required_fields():
    cfg = await workflows_config.get()
    wf = cfg.workflows["vpn_access"]
    assert wf.label == "VPN Access Request"
    assert wf.target_email_field == "target_email"
    assert len(wf.steps) >= 2


async def test_step_config_fields():
    cfg = await workflows_config.get()
    step = cfg.workflows["vpn_access"].steps[0]
    assert step.key == "request"
    assert step.type == "form"
    assert step.assignee == "requester"
    assert len(step.fields) >= 2
    assert "submit" in step.actions


async def test_step_field_config():
    cfg = await workflows_config.get()
    field = cfg.workflows["vpn_access"].steps[0].fields[0]
    assert field.name == "target_name"
    assert field.type == "text"
    assert field.required is True


async def test_notification_config():
    cfg = await workflows_config.get()
    notifs = cfg.workflows["vpn_access"].notifications
    assert len(notifs) >= 1
    assert notifs[0].event == "submit"
    assert "director" in notifs[0].notify


async def test_onboarding_has_partial_save_step():
    cfg = await workflows_config.get()
    newcomer_step = cfg.workflows["onboarding"].steps[1]
    assert newcomer_step.key == "newcomer_info"
    assert newcomer_step.partial_save is True
    assert newcomer_step.assignee == "target_person"


async def test_field_config_round_trips_visible_when_and_checkbox():
    """A FieldConfig with type=checkbox and visible_when serializes and
    re-parses without loss through the workflows ConfigSection."""
    from not_dot_net.config import FieldConfig, WorkflowConfig, WorkflowStepConfig
    from not_dot_net.backend.workflow_config import workflows_config, WorkflowsConfig

    cfg = WorkflowsConfig(workflows={
        "demo": WorkflowConfig(
            label="Demo",
            steps=[WorkflowStepConfig(
                key="s1", type="form",
                fields=[
                    FieldConfig(name="zrr", type="checkbox", label="zrr"),
                    FieldConfig(name="zrr_topic", type="text", label="zrr_topic",
                                visible_when={"zrr": True}),
                ],
                actions=["submit"],
            )],
        ),
    })
    await workflows_config.set(cfg)
    reloaded = await workflows_config.get()
    fields = reloaded.workflows["demo"].steps[0].fields
    assert fields[0].type == "checkbox"
    assert fields[1].visible_when == {"zrr": True}


def test_step_label_defaults_empty_for_legacy_configs():
    from not_dot_net.config import WorkflowStepConfig
    step = WorkflowStepConfig.model_validate({"key": "newcomer_info", "type": "form"})
    assert step.label == ""


def test_step_display_prefers_label():
    from not_dot_net.config import WorkflowStepConfig, step_display
    step = WorkflowStepConfig(key="newcomer_info", label="Newcomer information", type="form")
    assert step_display(step) == "Newcomer information"


def test_step_display_falls_back_to_prettified_key():
    from not_dot_net.config import WorkflowStepConfig, step_display
    step = WorkflowStepConfig(key="newcomer_info", type="form")
    assert step_display(step) == "Newcomer info"
