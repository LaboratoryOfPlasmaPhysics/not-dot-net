from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from not_dot_net.backend.app_config import section


# --- Workflow schema models (imported by notifications.py, workflow_engine.py, etc.) ---

class FieldConfig(BaseModel):
    name: str
    type: str  # text, email, textarea, date, select, file, phone, location, checkbox
    required: bool = False
    label: str = ""
    options_key: str | None = None  # for select: key in Settings (e.g. "teams")
    encrypted: bool = False
    half_width: bool = False
    visible_when: dict[str, Any] | None = None


def is_field_visible(field: FieldConfig, data: dict) -> bool:
    """A field is visible iff every (key, value) in `visible_when` matches `data`.
    No rule means always visible. Missing keys are treated as mismatches."""
    rule = field.visible_when
    if not rule:
        return True
    return all(data.get(k) == v for k, v in rule.items())


class NotificationRuleConfig(BaseModel):
    event: str  # submit, approve, reject
    step: str | None = None  # None = match any step
    notify: list[str]  # role names or contextual: requester, target_person


class StepEffectConfig(BaseModel):
    on_action: str
    kind: Literal[
        "ad_add_to_groups",
        "ad_remove_from_groups",
        "ad_enable_account",
        "ad_disable_account",
    ]
    params: dict[str, Any] = Field(default_factory=dict)


class WorkflowStepConfig(BaseModel):
    key: str
    type: str  # form, approval
    assignee_role: str | None = None
    assignee_permission: str | None = None
    assignee: str | None = None  # contextual: target_person, requester
    fields: list[FieldConfig] = []
    actions: list[str] = []
    partial_save: bool = False
    corrections_target: str | None = None
    effects: list[StepEffectConfig] = Field(default_factory=list)


class WorkflowConfig(BaseModel):
    label: str
    start_role: str = "staff"
    target_email_field: str | None = None
    steps: list[WorkflowStepConfig]
    notifications: list[NotificationRuleConfig] = []
    document_instructions: dict[str, list[str]] = {}


# --- OrgConfig section ---

class OrgConfig(BaseModel):
    app_name: str = "LPP Intranet"
    base_url: str = "http://localhost:8088"
    teams: list[str] = [
        "Plasma Physics",
        "Instrumentation",
        "Space Weather",
        "Theory & Simulation",
        "Administration",
    ]
    sites: list[str] = ["Palaiseau", "Jussieu"]
    employment_statuses: list[str] = ["CDD", "CDI", "Intern", "PhD", "PostDoc", "Visiting Researcher"]
    employers: list[str] = ["CNRS", "Sorbonne Université", "Polytechnique", "CNES", "Other"]
    transport_modes: list[str] = ["Train", "Avion", "Voiture personnelle", "Voiture de service", "Autre"]
    funding_sources: list[str] = [
        "Sorbonne Université",
        "Polytechnique",
        "CNES",
        "ANR",
        "ESA",
        "Autre",
    ]
    allowed_origins: list[str] = []


org_config = section("org", OrgConfig, label="Organization")


# --- FilesConfig section ---

DEFAULT_PROFILE_PHOTO_MAX_SIZE_MB = 2


class FilesConfig(BaseModel):
    profile_photo_max_size_mb: int = Field(default=DEFAULT_PROFILE_PHOTO_MAX_SIZE_MB, ge=1)


files_config = section("files", FilesConfig, label="Files")


# --- BookingsConfig section ---

class BookingsConfig(BaseModel):
    os_choices: list[str] = ["Windows", "Ubuntu", "Fedora"]
    software_tags: dict[str, list[str]] = {
        "Windows": ["Office 365", "MATLAB", "IDL", "Python (Anaconda)", "LabVIEW", "SolidWorks"],
        "Ubuntu": ["Python", "MATLAB", "IDL", "GCC", "LaTeX", "Docker"],
        "Fedora": ["Python", "MATLAB", "IDL", "GCC", "LaTeX", "Docker", "Toolbox"],
    }
    minimum_lead_days: int = Field(default=7, ge=0)
    resource_setup_buffer_days: int = Field(default=7, ge=0)
    max_booking_days: int = Field(default=183, ge=1)
    reminder_lead_days: list[int] = Field(default_factory=lambda: [1])

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_reminder_lead_days(cls, data):
        if isinstance(data, dict) and "reminder_lead_days" in data:
            value = data.get("reminder_lead_days")
            if value is None:
                data = {**data, "reminder_lead_days": []}
            elif isinstance(value, int):
                data = {**data, "reminder_lead_days": [value]}
        return data

    @model_validator(mode="after")
    def _validate_reminder_lead_days(self):
        normalized = sorted({int(day) for day in self.reminder_lead_days})
        invalid = [day for day in normalized if day < 0 or day > self.max_booking_days]
        if invalid:
            raise ValueError(
                "reminder_lead_days must be between 0 and max_booking_days"
            )
        self.reminder_lead_days = normalized
        return self


bookings_config = section("bookings", BookingsConfig, label="Bookings")


# --- DashboardConfig section ---

class DashboardConfig(BaseModel):
    urgency_fresh_days: int = 2
    urgency_aging_days: int = 7


dashboard_config = section("dashboard", DashboardConfig, label="Dashboard")
