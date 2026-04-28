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


async def test_bookings_config_set_os_choices():
    custom = BookingsConfig(os_choices=["CustomOS"], software_tags={})
    await bookings_config.set(custom)
    cfg = await bookings_config.get()
    assert cfg.os_choices == ["CustomOS"]


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


def test_admin_settings_detects_complex_schema_for_dict_fields():
    from not_dot_net.frontend.admin_settings import _is_complex

    class DictSettings(BaseModel):
        values: dict[str, list[str]]

    assert _is_complex(DictSettings) is True


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
