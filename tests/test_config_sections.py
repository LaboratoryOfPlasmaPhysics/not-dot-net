"""Tests for config sections defined in their owner modules."""

import pytest


# --- OrgConfig ---

async def test_org_config_defaults():
    from not_dot_net.config import org_config
    cfg = await org_config.get()
    assert cfg.app_name == "LPP Intranet"
    assert cfg.base_url == "http://localhost:8088"
    assert len(cfg.teams) > 0
    assert len(cfg.sites) > 0
    assert "PhD" in cfg.employment_statuses
    assert "CNRS" in cfg.employers
    assert isinstance(cfg.allowed_origins, list)


async def test_org_config_roundtrip():
    from not_dot_net.config import org_config, OrgConfig
    custom = OrgConfig(
        app_name="Test App",
        base_url="https://intranet.example.test",
        teams=["A"],
        sites=["B"],
        employment_statuses=["Permanent"],
        employers=["Example Lab"],
        allowed_origins=["http://x"],
    )
    await org_config.set(custom)
    result = await org_config.get()
    assert result.app_name == "Test App"
    assert result.base_url == "https://intranet.example.test"
    assert result.teams == ["A"]
    assert result.sites == ["B"]
    assert result.employment_statuses == ["Permanent"]
    assert result.employers == ["Example Lab"]
    assert result.allowed_origins == ["http://x"]


async def test_org_config_registered():
    from not_dot_net.backend.app_config import get_registry
    from not_dot_net.config import org_config  # noqa: F401 — trigger registration
    assert "org" in get_registry()


# --- FilesConfig ---

async def test_files_config_defaults():
    from not_dot_net.config import files_config
    cfg = await files_config.get()
    assert cfg.profile_photo_max_size_mb == 2


async def test_files_config_roundtrip():
    from not_dot_net.config import files_config, FilesConfig
    custom = FilesConfig(profile_photo_max_size_mb=5)
    await files_config.set(custom)
    result = await files_config.get()
    assert result.profile_photo_max_size_mb == 5


async def test_files_config_registered():
    from not_dot_net.backend.app_config import get_registry
    from not_dot_net.config import files_config  # noqa: F401
    assert "files" in get_registry()


# --- BookingsConfig ---

async def test_bookings_config_defaults():
    from not_dot_net.config import bookings_config
    cfg = await bookings_config.get()
    assert "Windows" in cfg.os_choices
    assert "Windows" in cfg.software_tags
    assert cfg.minimum_lead_days == 7
    assert cfg.resource_setup_buffer_days == 7
    assert cfg.max_booking_days == 183
    assert cfg.reminder_lead_days == [1]


async def test_bookings_config_roundtrip():
    from not_dot_net.config import bookings_config, BookingsConfig
    custom = BookingsConfig(
        os_choices=["Linux"],
        software_tags={"Linux": ["vim"]},
        minimum_lead_days=4,
        resource_setup_buffer_days=3,
        max_booking_days=31,
        reminder_lead_days=[7, 1, 0],
    )
    await bookings_config.set(custom)
    result = await bookings_config.get()
    assert result.os_choices == ["Linux"]
    assert result.software_tags == {"Linux": ["vim"]}
    assert result.minimum_lead_days == 4
    assert result.resource_setup_buffer_days == 3
    assert result.max_booking_days == 31
    assert result.reminder_lead_days == [0, 1, 7]


async def test_bookings_config_registered():
    from not_dot_net.backend.app_config import get_registry
    from not_dot_net.config import bookings_config  # noqa: F401
    assert "bookings" in get_registry()


# --- LdapConfig ---

async def test_ldap_config_defaults():
    from not_dot_net.backend.auth.ldap import ldap_config
    cfg = await ldap_config.get()
    assert cfg.url == ""
    assert cfg.domain == "example.com"
    assert cfg.port == 389
    assert cfg.tls_mode == "none"
    assert cfg.tls_verify is True
    assert cfg.user_filter == ""
    assert cfg.auto_provision is True


async def test_ldap_config_roundtrip():
    from not_dot_net.backend.auth.ldap import ldap_config, LdapConfig, TlsMode
    custom = LdapConfig(
        url="ldap://ad.corp, ldap://backup.corp",
        domain="corp.com",
        base_dn="dc=corp,dc=com",
        port=636,
        tls_mode=TlsMode.START_TLS,
        tls_verify=False,
        user_filter="(memberOf=CN=Intranet,DC=corp,DC=com)",
        auto_provision=False,
    )
    await ldap_config.set(custom)
    result = await ldap_config.get()
    assert result.url == "ldap://ad.corp, ldap://backup.corp"
    assert result.domain == "corp.com"
    assert result.base_dn == "dc=corp,dc=com"
    assert result.port == 636
    assert result.tls_mode == TlsMode.START_TLS
    assert result.tls_verify is False
    assert result.user_filter == "(memberOf=CN=Intranet,DC=corp,DC=com)"
    assert result.auto_provision is False


async def test_ldap_config_registered():
    from not_dot_net.backend.app_config import get_registry
    from not_dot_net.backend.auth.ldap import ldap_config  # noqa: F401
    assert "ldap" in get_registry()


# --- MailConfig ---

async def test_mail_config_defaults():
    from not_dot_net.backend.mail import mail_config, SmtpTlsMode
    cfg = await mail_config.get()
    assert cfg.smtp_host == "localhost"
    assert cfg.smtp_port == 587
    assert cfg.smtp_tls_mode == SmtpTlsMode.NONE
    assert cfg.smtp_user == ""
    assert cfg.smtp_password == ""
    assert cfg.from_address == "noreply@not-dot-net.dev"
    assert cfg.dev_mode is True
    assert cfg.dev_catch_all == ""


async def test_mail_config_roundtrip():
    from not_dot_net.backend.mail import mail_config, MailConfig, SmtpTlsMode
    custom = MailConfig(
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_tls_mode=SmtpTlsMode.SMTPS,
        smtp_user="smtp-user",
        smtp_password="smtp-secret",
        from_address="intranet@example.com",
        dev_mode=False,
        dev_catch_all="catchall@example.com",
    )
    await mail_config.set(custom)
    result = await mail_config.get()
    assert result.smtp_host == "smtp.example.com"
    assert result.smtp_port == 465
    assert result.smtp_tls_mode == SmtpTlsMode.SMTPS
    assert result.smtp_user == "smtp-user"
    assert result.smtp_password == "smtp-secret"
    assert result.from_address == "intranet@example.com"
    assert result.dev_mode is False
    assert result.dev_catch_all == "catchall@example.com"


async def test_mail_config_legacy_smtp_tls_field_migrates():
    """A row persisted under the old `smtp_tls: bool` shape rehydrates
    cleanly: True → STARTTLS, False → NONE."""
    from not_dot_net.backend.mail import MailConfig, SmtpTlsMode

    legacy_true = MailConfig.model_validate({"smtp_tls": True})
    assert legacy_true.smtp_tls_mode == SmtpTlsMode.STARTTLS

    legacy_false = MailConfig.model_validate({"smtp_tls": False})
    assert legacy_false.smtp_tls_mode == SmtpTlsMode.NONE

    # New shape wins if both are present (defensive)
    both = MailConfig.model_validate({"smtp_tls": True, "smtp_tls_mode": "smtps"})
    assert both.smtp_tls_mode == SmtpTlsMode.SMTPS


async def test_mail_config_registered():
    from not_dot_net.backend.app_config import get_registry
    from not_dot_net.backend.mail import mail_config  # noqa: F401
    assert "mail" in get_registry()


# --- WorkflowsConfig ---

async def test_workflows_config_defaults():
    from not_dot_net.backend.workflow_service import workflows_config
    cfg = await workflows_config.get()
    assert cfg.token_expiry_days == 30
    assert cfg.verification_code_expiry_minutes == 15
    assert cfg.max_upload_size_mb == 10
    assert "vpn_access" in cfg.workflows
    assert "onboarding" in cfg.workflows
    assert cfg.workflows["vpn_access"].label == "VPN Access Request"


async def test_workflows_config_onboarding_v2_defaults():
    from not_dot_net.backend.workflow_service import workflows_config

    cfg = await workflows_config.get()
    onboarding = cfg.workflows["onboarding"]
    step_keys = [step.key for step in onboarding.steps]

    assert step_keys == [
        "initiation",
        "newcomer_info",
        "admin_validation",
        "it_account_creation",
    ]
    assert onboarding.target_email_field == "contact_email"
    assert "PhD" in onboarding.document_instructions

    newcomer_info = onboarding.steps[1]
    encrypted_fields = {
        field.name
        for field in newcomer_info.fields
        if field.type == "file" and field.encrypted
    }
    assert encrypted_fields == {"id_document", "bank_details", "photo"}
    assert newcomer_info.partial_save is True

    admin_validation = onboarding.steps[2]
    assert admin_validation.assignee_permission == "access_personal_data"
    assert admin_validation.corrections_target == "newcomer_info"


async def test_workflows_config_roundtrip():
    from not_dot_net.backend.workflow_service import workflows_config, WorkflowsConfig
    from not_dot_net.config import WorkflowConfig, WorkflowStepConfig
    custom = WorkflowsConfig(
        token_expiry_days=7,
        verification_code_expiry_minutes=5,
        max_upload_size_mb=25,
        workflows={
            "test_wf": WorkflowConfig(
                label="Test",
                target_email_field="target_email",
                document_instructions={"_default": ["ID"]},
                steps=[WorkflowStepConfig(key="s1", type="form", actions=["submit"])],
            ),
        },
    )
    await workflows_config.set(custom)
    result = await workflows_config.get()
    assert result.token_expiry_days == 7
    assert result.verification_code_expiry_minutes == 5
    assert result.max_upload_size_mb == 25
    assert "test_wf" in result.workflows
    assert result.workflows["test_wf"].label == "Test"
    assert result.workflows["test_wf"].target_email_field == "target_email"
    assert result.workflows["test_wf"].document_instructions == {"_default": ["ID"]}


async def test_workflows_config_registered():
    from not_dot_net.backend.app_config import get_registry
    from not_dot_net.backend.workflow_service import workflows_config  # noqa: F401
    assert "workflows" in get_registry()


# --- DashboardConfig ---

async def test_dashboard_config_defaults():
    from not_dot_net.config import dashboard_config
    cfg = await dashboard_config.get()
    assert cfg.urgency_fresh_days == 2
    assert cfg.urgency_aging_days == 7


async def test_dashboard_config_roundtrip():
    from not_dot_net.config import dashboard_config, DashboardConfig
    custom = DashboardConfig(urgency_fresh_days=1, urgency_aging_days=14)
    await dashboard_config.set(custom)
    result = await dashboard_config.get()
    assert result.urgency_fresh_days == 1
    assert result.urgency_aging_days == 14


async def test_dashboard_config_registered():
    from not_dot_net.backend.app_config import get_registry
    from not_dot_net.config import dashboard_config  # noqa: F401
    assert "dashboard" in get_registry()


# --- AdAccountConfig ---

async def test_ad_account_config_defaults():
    from not_dot_net.backend.ad_account_config import ad_account_config
    cfg = await ad_account_config.get()
    assert cfg.uid_min == 10000
    assert cfg.uid_max == 60000
    assert cfg.mail_template.endswith("@lpp.polytechnique.fr")
    assert cfg.eligible_groups == []


async def test_ad_account_config_roundtrip():
    from not_dot_net.backend.ad_account_config import ad_account_config, AdAccountConfig
    custom = AdAccountConfig(
        uid_min=20000,
        uid_max=50000,
        default_gid_number=20000,
        default_login_shell="/bin/zsh",
        home_directory_template="/home/{sam}",
        mail_template="{first}.{last}@example.fr",
        users_ous=["CN=Users,DC=example,DC=com"],
        eligible_groups=["CN=intranet,DC=example,DC=com"],
        default_groups_by_status={"Intern": ["CN=interns,DC=example,DC=com"]},
        password_length=20,
    )
    await ad_account_config.set(custom)
    result = await ad_account_config.get()
    assert result.uid_min == 20000
    assert result.uid_max == 50000
    assert result.default_gid_number == 20000
    assert result.default_login_shell == "/bin/zsh"
    assert result.users_ous == ["CN=Users,DC=example,DC=com"]
    assert result.eligible_groups == ["CN=intranet,DC=example,DC=com"]
    assert result.default_groups_by_status == {"Intern": ["CN=interns,DC=example,DC=com"]}
    assert result.password_length == 20


async def test_ad_account_config_registered():
    from not_dot_net.backend.app_config import get_registry
    from not_dot_net.backend.ad_account_config import ad_account_config  # noqa: F401
    assert "ad_account" in get_registry()
