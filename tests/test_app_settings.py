import pytest
from enum import Enum
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pydantic import BaseModel

from not_dot_net.config import bookings_config, BookingsConfig


async def test_bookings_config_defaults():
    cfg = await bookings_config.get()
    assert "Windows" in cfg.os_choices
    assert "Ubuntu" in cfg.software_tags
    assert cfg.minimum_lead_days == 7
    assert cfg.resource_setup_buffer_days == 7
    assert cfg.max_booking_days == 183
    assert cfg.reminder_lead_days == [1]


async def test_bookings_config_set_os_choices():
    custom = BookingsConfig(
        os_choices=["CustomOS"],
        software_tags={},
        minimum_lead_days=5,
        resource_setup_buffer_days=4,
        max_booking_days=42,
        reminder_lead_days=[7, 1, 0],
    )
    await bookings_config.set(custom)
    cfg = await bookings_config.get()
    assert cfg.os_choices == ["CustomOS"]
    assert cfg.minimum_lead_days == 5
    assert cfg.resource_setup_buffer_days == 4
    assert cfg.max_booking_days == 42
    assert cfg.reminder_lead_days == [0, 1, 7]


def test_bookings_config_accepts_legacy_single_reminder_lead_day():
    cfg = BookingsConfig.model_validate({"reminder_lead_days": 7})
    assert cfg.reminder_lead_days == [7]


def test_bookings_config_rejects_reminders_after_max_booking_days():
    with pytest.raises(ValueError, match="reminder_lead_days"):
        BookingsConfig(max_booking_days=10, reminder_lead_days=[11])


async def test_bookings_config_reset():
    custom = BookingsConfig(os_choices=["X"])
    await bookings_config.set(custom)
    await bookings_config.reset()
    cfg = await bookings_config.get()
    assert cfg == BookingsConfig()


def test_admin_settings_detects_enum_fields():
    from not_dot_net.frontend.admin_settings import _is_enum

    class Mode(str, Enum):
        DEV = "dev"
        PROD = "prod"

    assert _is_enum(Mode) is True
    assert _is_enum(str) is False
    assert _is_enum(list[str]) is False


def test_admin_settings_detects_list_int_fields():
    from not_dot_net.frontend.admin_settings import _is_list_int

    assert _is_list_int(list[int]) is True
    assert _is_list_int(list[str]) is False
    assert _is_list_int(int) is False


def test_admin_settings_dict_str_list_str_is_not_complex():
    """dict[str, list[str]] is now editable via keyed_chip_editor — not complex."""
    from not_dot_net.frontend.admin_settings import _is_complex

    class DictSettings(BaseModel):
        values: dict[str, list[str]] = {}

    assert _is_complex(DictSettings) is False


def test_admin_settings_nested_basemodel_still_complex():
    from not_dot_net.frontend.admin_settings import _is_complex

    class Inner(BaseModel):
        x: int = 0

    class Outer(BaseModel):
        nested: dict[str, Inner] = {}

    assert _is_complex(Outer) is True


def test_admin_settings_detects_complex_schema_for_nested_models():
    from not_dot_net.frontend.admin_settings import _is_complex

    class Nested(BaseModel):
        enabled: bool = True

    class NestedSettings(BaseModel):
        nested: Nested

    assert _is_complex(NestedSettings) is True


def test_admin_settings_treats_scalar_and_list_schema_as_simple():
    from not_dot_net.frontend.admin_settings import _is_complex

    class SimpleSettings(BaseModel):
        name: str = "test"
        enabled: bool = True
        retries: int = 3
        origins: list[str] = []

    assert _is_complex(SimpleSettings) is False


async def test_import_upload_logs_audit_on_success():
    from tests.test_import_upload import _make_event
    from not_dot_net.frontend.admin_settings import _handle_import_upload

    admin = SimpleNamespace(id="00000000-0000-0000-0000-000000000000", email="admin@test.local")
    payload = {
        "version": 1,
        "resources": [
            {"name": "settings-audit-pc", "resource_type": "desktop"},
        ],
    }

    with (
        patch("not_dot_net.frontend.admin_settings.ui") as mock_ui,
        patch("not_dot_net.frontend.admin_settings.log_audit", new_callable=AsyncMock) as audit,
        patch("not_dot_net.frontend.admin_settings.t", side_effect=lambda k: k),
    ):
        await _handle_import_upload(_make_event(payload), replace=False, user=admin)

    mock_ui.notify.assert_called_once()
    audit.assert_awaited_once()
    args, kwargs = audit.await_args
    assert args[:2] == ("settings", "import")
    assert kwargs["actor_id"] == admin.id
    assert kwargs["actor_email"] == admin.email
    assert "resources" in kwargs["detail"]


async def test_import_upload_does_not_audit_invalid_json():
    from tests.test_import_upload import FakeFileUpload, FakeUploadEvent
    from not_dot_net.frontend.admin_settings import _handle_import_upload

    admin = SimpleNamespace(id="00000000-0000-0000-0000-000000000000", email="admin@test.local")
    event = FakeUploadEvent(file=FakeFileUpload(b"not json{{{"))

    with (
        patch("not_dot_net.frontend.admin_settings.ui"),
        patch("not_dot_net.frontend.admin_settings.log_audit", new_callable=AsyncMock) as audit,
        patch("not_dot_net.frontend.admin_settings.t", side_effect=lambda k: k),
    ):
        await _handle_import_upload(event, replace=False, user=admin)

    audit.assert_not_awaited()


class TestSecretFieldsAreMasked:
    """S3 — the auto-generated settings form rendered every `str` field as a
    plain ui.input, so smtp_password sat in the DOM in clear for anyone holding
    manage_settings (and anyone shoulder-surfing them)."""

    def test_smtp_password_is_marked_secret(self):
        from not_dot_net.backend.mail import MailConfig

        field = MailConfig.model_fields["smtp_password"]
        assert (field.json_schema_extra or {}).get("secret") is True

    def test_non_secret_fields_are_not_marked(self):
        from not_dot_net.backend.mail import MailConfig

        for name in ("smtp_host", "smtp_user", "from_address"):
            field = MailConfig.model_fields[name]
            assert not (field.json_schema_extra or {}).get("secret"), name

    def test_renderer_consults_the_marker(self):
        """The form must consult the marker, not just have one available."""
        from not_dot_net.backend.mail import MailConfig
        from not_dot_net.frontend.admin_settings import _is_secret_field

        assert _is_secret_field(MailConfig.model_fields["smtp_password"]) is True
        assert _is_secret_field(MailConfig.model_fields["smtp_host"]) is False
