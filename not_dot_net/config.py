from pydantic import BaseModel

from not_dot_net.backend.app_config import section


# --- Workflow schema models (imported by notifications.py, workflow_engine.py, etc.) ---

class FieldConfig(BaseModel):
    name: str
    type: str  # text, email, textarea, date, select, file
    required: bool = False
    label: str = ""
    options_key: str | None = None  # for select: key in Settings (e.g. "teams")


class NotificationRuleConfig(BaseModel):
    event: str  # submit, approve, reject
    step: str | None = None  # None = match any step
    notify: list[str]  # role names or contextual: requester, target_person


class WorkflowStepConfig(BaseModel):
    key: str
    type: str  # form, approval
    assignee_role: str | None = None
    assignee: str | None = None  # contextual: target_person, requester
    fields: list[FieldConfig] = []
    actions: list[str] = []
    partial_save: bool = False


class WorkflowConfig(BaseModel):
    label: str
    start_role: str = "staff"
    target_email_field: str | None = None
    steps: list[WorkflowStepConfig]
    notifications: list[NotificationRuleConfig] = []


# --- OrgConfig section ---

class OrgConfig(BaseModel):
    app_name: str = "LPP Intranet"
    teams: list[str] = [
        "Plasma Physics",
        "Instrumentation",
        "Space Weather",
        "Theory & Simulation",
        "Administration",
    ]
    sites: list[str] = ["Palaiseau", "Jussieu"]
    allowed_origins: list[str] = []


org_config = section("org", OrgConfig, label="Organization")


# --- BookingsConfig section ---

class BookingsConfig(BaseModel):
    os_choices: list[str] = ["Windows", "Ubuntu", "Fedora"]
    software_tags: dict[str, list[str]] = {
        "Windows": ["Office 365", "MATLAB", "IDL", "Python (Anaconda)", "LabVIEW", "SolidWorks"],
        "Ubuntu": ["Python", "MATLAB", "IDL", "GCC", "LaTeX", "Docker"],
        "Fedora": ["Python", "MATLAB", "IDL", "GCC", "LaTeX", "Docker", "Toolbox"],
    }


bookings_config = section("bookings", BookingsConfig, label="Bookings")


# --- Backward-compat shims (removed in Task 7) ---
# These allow modules not yet migrated (users.py, frontend, tests) to import
# without crashing at module load time. Calling them at runtime will fail.

# Re-export old names so `from not_dot_net.config import MailSettings` etc. still resolve
MailSettings = None  # replaced by backend.mail.MailConfig
LDAPSettings = None  # replaced by backend.auth.ldap.LdapConfig


def get_settings():
    raise RuntimeError(
        "get_settings() has been removed. "
        "Use the config section for the module you need (e.g. org_config, mail_config, etc.)."
    )


def init_settings(config_file=None):
    raise RuntimeError(
        "init_settings() has been removed. "
        "Config sections are now DB-backed via app_config.section()."
    )
