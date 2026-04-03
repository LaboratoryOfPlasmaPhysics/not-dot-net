"""Tests for config sections defined in their owner modules."""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from not_dot_net.backend.db import Base
import not_dot_net.backend.db as db_module


@pytest.fixture(autouse=True)
async def setup_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    old_engine, old_session = db_module._engine, db_module._async_session_maker
    db_module._engine = engine
    db_module._async_session_maker = session_maker
    import not_dot_net.backend.app_config  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()
    db_module._engine, db_module._async_session_maker = old_engine, old_session


# --- OrgConfig ---

async def test_org_config_defaults():
    from not_dot_net.config import org_config
    cfg = await org_config.get()
    assert cfg.app_name == "LPP Intranet"
    assert len(cfg.teams) > 0
    assert len(cfg.sites) > 0
    assert isinstance(cfg.allowed_origins, list)


async def test_org_config_roundtrip():
    from not_dot_net.config import org_config, OrgConfig
    custom = OrgConfig(app_name="Test App", teams=["A"], sites=["B"], allowed_origins=["http://x"])
    await org_config.set(custom)
    result = await org_config.get()
    assert result.app_name == "Test App"
    assert result.teams == ["A"]


async def test_org_config_registered():
    from not_dot_net.backend.app_config import get_registry
    from not_dot_net.config import org_config  # noqa: F401 — trigger registration
    assert "org" in get_registry()


# --- BookingsConfig ---

async def test_bookings_config_defaults():
    from not_dot_net.config import bookings_config
    cfg = await bookings_config.get()
    assert "Windows" in cfg.os_choices
    assert "Windows" in cfg.software_tags


async def test_bookings_config_roundtrip():
    from not_dot_net.config import bookings_config, BookingsConfig
    custom = BookingsConfig(os_choices=["Linux"], software_tags={"Linux": ["vim"]})
    await bookings_config.set(custom)
    result = await bookings_config.get()
    assert result.os_choices == ["Linux"]


async def test_bookings_config_registered():
    from not_dot_net.backend.app_config import get_registry
    from not_dot_net.config import bookings_config  # noqa: F401
    assert "bookings" in get_registry()


# --- LdapConfig ---

async def test_ldap_config_defaults():
    from not_dot_net.backend.auth.ldap import ldap_config
    cfg = await ldap_config.get()
    assert cfg.url == "ldap://localhost"
    assert cfg.domain == "example.com"
    assert cfg.port == 389


async def test_ldap_config_roundtrip():
    from not_dot_net.backend.auth.ldap import ldap_config, LdapConfig
    custom = LdapConfig(url="ldap://ad.corp", domain="corp.com", base_dn="dc=corp,dc=com", port=636)
    await ldap_config.set(custom)
    result = await ldap_config.get()
    assert result.url == "ldap://ad.corp"
    assert result.port == 636


async def test_ldap_config_registered():
    from not_dot_net.backend.app_config import get_registry
    from not_dot_net.backend.auth.ldap import ldap_config  # noqa: F401
    assert "ldap" in get_registry()


# --- MailConfig ---

async def test_mail_config_defaults():
    from not_dot_net.backend.mail import mail_config
    cfg = await mail_config.get()
    assert cfg.smtp_host == "localhost"
    assert cfg.dev_mode is True


async def test_mail_config_roundtrip():
    from not_dot_net.backend.mail import mail_config, MailConfig
    custom = MailConfig(smtp_host="smtp.example.com", smtp_port=465, dev_mode=False)
    await mail_config.set(custom)
    result = await mail_config.get()
    assert result.smtp_host == "smtp.example.com"
    assert result.dev_mode is False


async def test_mail_config_registered():
    from not_dot_net.backend.app_config import get_registry
    from not_dot_net.backend.mail import mail_config  # noqa: F401
    assert "mail" in get_registry()


# --- WorkflowsConfig ---

async def test_workflows_config_defaults():
    from not_dot_net.backend.workflow_service import workflows_config
    cfg = await workflows_config.get()
    assert "vpn_access" in cfg.workflows
    assert "onboarding" in cfg.workflows
    assert cfg.workflows["vpn_access"].label == "VPN Access Request"


async def test_workflows_config_roundtrip():
    from not_dot_net.backend.workflow_service import workflows_config, WorkflowsConfig
    from not_dot_net.config import WorkflowConfig, WorkflowStepConfig
    custom = WorkflowsConfig(workflows={
        "test_wf": WorkflowConfig(
            label="Test",
            steps=[WorkflowStepConfig(key="s1", type="form", actions=["submit"])],
        ),
    })
    await workflows_config.set(custom)
    result = await workflows_config.get()
    assert "test_wf" in result.workflows
    assert result.workflows["test_wf"].label == "Test"


async def test_workflows_config_registered():
    from not_dot_net.backend.app_config import get_registry
    from not_dot_net.backend.workflow_service import workflows_config  # noqa: F401
    assert "workflows" in get_registry()
