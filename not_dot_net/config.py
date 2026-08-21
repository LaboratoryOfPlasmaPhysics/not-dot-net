from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from not_dot_net.backend.app_config import section

if TYPE_CHECKING:
    from not_dot_net.backend.field_definitions import FieldDefinition


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


class FieldRef(BaseModel):
    """A workflow step's reference to a shared FieldDefinition.

    Every override is Optional; None means "inherit live from the definition".
    `name` is intentionally absent — a reference's resolved name is the
    definition key, so a shared field always stores under one data key.
    """
    ref: str
    type: str | None = None
    label: str | None = None
    required: bool | None = None
    options_key: str | None = None
    encrypted: bool | None = None
    half_width: bool | None = None
    visible_when: dict[str, Any] | None = None   # step-local only (never in the definition)


def resolve_field_ref(ref: "FieldRef", defn: "FieldDefinition") -> FieldConfig:
    """Merge a reference over its definition into a concrete FieldConfig."""
    def pick(override, base):
        return override if override is not None else base
    return FieldConfig(
        name=defn.key,
        type=pick(ref.type, defn.type),
        label=pick(ref.label, defn.label),
        required=pick(ref.required, defn.required),
        options_key=pick(ref.options_key, defn.options_key),
        encrypted=pick(ref.encrypted, defn.encrypted),
        half_width=pick(ref.half_width, defn.half_width),
        visible_when=ref.visible_when,
    )


StepField = Annotated[FieldConfig | FieldRef, Field(union_mode="left_to_right")]


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
    label: str = ""
    type: str  # form, approval
    assignee_role: str | None = None
    assignee_permission: str | None = None
    assignee: str | None = None  # contextual: target_person, requester
    fields: list[StepField] = []
    actions: list[str] = []
    partial_save: bool = False
    corrections_target: str | None = None
    effects: list[StepEffectConfig] = Field(default_factory=list)


def step_display(step: WorkflowStepConfig) -> str:
    """Human name for a step: explicit label, else the key prettified."""
    return step.label or step.key.replace("_", " ").capitalize()


class TenureHookConfig(BaseModel):
    """Record a UserTenure when a request of this workflow COMPLETES.

    Declaring it here is what keeps the engine generic: without it the service
    layer had to test `req.type == "onboarding"` and read fixed field names, so
    renaming the workflow in the admin editor silently stopped recording
    tenures. The field names below are the request-data keys to read.
    """
    status_field: str = "status"
    employer_field: str = "employer"
    start_date_field: str = "start_date"
    returning_user_field: str = "returning_user_id"


class WorkflowConfig(BaseModel):
    label: str
    target_email_field: str | None = None
    steps: list[WorkflowStepConfig]
    notifications: list[NotificationRuleConfig] = []
    document_instructions: dict[str, list[str]] = {}
    tenure: TenureHookConfig | None = None


# --- OrgConfig section ---

class OrgConfig(BaseModel):
    app_name: str = "LPP Intranet"
    base_url: str = "http://localhost:8088"


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
