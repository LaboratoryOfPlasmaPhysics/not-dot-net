"""Default (seed) workflow definitions for WorkflowsConfig.

Pure data: these populate the `workflows` config section on first use and
are fully editable afterwards via Settings → Workflows.
"""

from not_dot_net.config import (
    FieldConfig,
    TenureHookConfig,
    NotificationRuleConfig,
    StepEffectConfig,
    WorkflowConfig,
    WorkflowStepConfig,
)


def default_workflows() -> dict[str, WorkflowConfig]:
    return {
        "vpn_access": WorkflowConfig(
            label="VPN Access Request",
            target_email_field="target_email",
            steps=[
                WorkflowStepConfig(
                    key="request",
                    label="Request",
                    type="form",
                    assignee="requester",
                    fields=[
                        FieldConfig(name="target_name", type="text", required=True, label="target_name"),
                        FieldConfig(name="target_email", type="email", required=True, label="target_email"),
                        FieldConfig(name="justification", type="textarea", required=False, label="justification"),
                    ],
                    actions=["submit"],
                ),
                WorkflowStepConfig(
                    key="approval",
                    label="Approval",
                    type="approval",
                    assignee_permission="approve_workflows",
                    actions=["approve", "reject"],
                    effects=[
                        StepEffectConfig(
                            on_action="approve",
                            kind="ad_add_to_groups",
                            params={"groups": []},  # admin fills in via the editor
                        ),
                    ],
                ),
            ],
            notifications=[
                NotificationRuleConfig(event="submit", step="request", notify=["director"]),
                NotificationRuleConfig(event="approve", notify=["requester", "target_person"]),
                NotificationRuleConfig(event="reject", notify=["requester"]),
            ],
        ),
        "onboarding": WorkflowConfig(
            label="Onboarding",
            target_email_field="contact_email",
            tenure=TenureHookConfig(),
            document_instructions={
                "Intern": ["ID document", "Internship agreement", "Photo"],
                "PhD": ["ID document", "Bank details (RIB)", "Photo", "PhD enrollment certificate"],
                "_default": ["ID document", "Bank details (RIB)", "Photo"],
            },
            steps=[
                WorkflowStepConfig(
                    key="initiation",
                    label="Initiation",
                    type="form",
                    assignee="requester",
                    fields=[
                        FieldConfig(name="contact_email", type="email", required=True, label="contact_email"),
                        FieldConfig(name="status", type="select", required=True, label="status", options_key="employment_statuses"),
                        FieldConfig(name="employer", type="select", required=True, label="employer", options_key="employers"),
                    ],
                    actions=["submit"],
                ),
                WorkflowStepConfig(
                    key="newcomer_info",
                    label="Newcomer information",
                    type="form",
                    assignee="target_person",
                    partial_save=True,
                    fields=[
                        FieldConfig(name="first_name", type="text", required=True, label="first_name", half_width=True),
                        FieldConfig(name="last_name", type="text", required=True, label="last_name", half_width=True),
                        FieldConfig(name="phone", type="phone", label="phone", half_width=True),
                        FieldConfig(name="emergency_contact", type="phone", label="emergency_contact", half_width=True),
                        FieldConfig(name="address", type="location", label="address"),
                        FieldConfig(name="id_document", type="file", required=True, label="id_document", encrypted=True),
                        FieldConfig(name="bank_details", type="file", required=True, label="bank_details", encrypted=True),
                        FieldConfig(name="photo", type="file", label="photo", encrypted=True),
                    ],
                    actions=["submit"],
                ),
                WorkflowStepConfig(
                    key="admin_validation",
                    label="Admin validation",
                    type="approval",
                    assignee_permission="access_personal_data",
                    actions=["approve", "request_corrections", "reject"],
                    corrections_target="newcomer_info",
                ),
                WorkflowStepConfig(
                    key="it_account_creation",
                    label="IT account creation",
                    type="ad_account_creation",
                    assignee_permission="manage_users",
                    fields=[],
                    actions=["complete"],
                ),
            ],
            notifications=[
                NotificationRuleConfig(event="submit", step="initiation", notify=["target_person"]),
                NotificationRuleConfig(event="submit", step="newcomer_info", notify=["permission:access_personal_data"]),
                NotificationRuleConfig(event="approve", step="admin_validation", notify=["permission:manage_users", "requester"]),
                NotificationRuleConfig(event="request_corrections", step="admin_validation", notify=["target_person"]),
                NotificationRuleConfig(event="reject", notify=["requester"]),
                NotificationRuleConfig(event="complete", step="it_account_creation", notify=["requester", "target_person"]),
            ],
        ),
        "ordre_de_mission": WorkflowConfig(
            label="Ordre de Mission",
            steps=[
                WorkflowStepConfig(
                    key="submission",
                    label="Submission",
                    type="form",
                    assignee="requester",
                    fields=[
                        FieldConfig(name="mission_subject", type="textarea", required=True, label="mission_subject"),
                        FieldConfig(name="destination", type="location", required=True, label="destination"),
                        FieldConfig(name="conference_or_lab", type="text", required=True, label="conference_or_lab"),
                        FieldConfig(name="departure_date", type="date", required=True, label="departure_date", half_width=True),
                        FieldConfig(name="return_date", type="date", required=True, label="return_date", half_width=True),
                        FieldConfig(name="transport_mode", type="select", required=True, label="transport_mode", options_key="transport_modes", half_width=True),
                        FieldConfig(name="funding_source", type="select", required=True, label="funding_source", options_key="funding_sources", half_width=True),
                        FieldConfig(name="estimated_cost", type="text", label="estimated_cost"),
                        FieldConfig(name="additional_info", type="textarea", label="additional_info"),
                        FieldConfig(name="invitation_or_program", type="file", label="invitation_or_program"),
                    ],
                    actions=["submit"],
                ),
                WorkflowStepConfig(
                    key="admin_validation",
                    label="Admin validation",
                    type="approval",
                    assignee_permission="approve_workflows",
                    actions=["approve", "request_corrections", "reject"],
                    corrections_target="submission",
                ),
                WorkflowStepConfig(
                    key="director_approval",
                    label="Director approval",
                    type="approval",
                    assignee_permission="approve_workflows",
                    actions=["approve", "reject"],
                ),
            ],
            notifications=[
                NotificationRuleConfig(event="submit", step="submission", notify=["permission:approve_workflows"]),
                NotificationRuleConfig(event="approve", step="admin_validation", notify=["director"]),
                NotificationRuleConfig(event="request_corrections", step="admin_validation", notify=["requester"]),
                NotificationRuleConfig(event="approve", step="director_approval", notify=["requester"]),
                NotificationRuleConfig(event="reject", notify=["requester"]),
            ],
        ),
    }
