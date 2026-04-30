"""Test that the import upload handler uses the correct NiceGUI upload API."""

import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from not_dot_net.backend.data_io import export_resources
from not_dot_net.frontend.admin_settings import _handle_import_upload


@dataclass
class FakeFileUpload:
    _data: bytes

    async def json(self, encoding: str = "utf-8"):
        return json.loads(self._data.decode(encoding))


@dataclass
class FakeUploadEvent:
    file: FakeFileUpload


def _make_event(payload) -> FakeUploadEvent:
    return FakeUploadEvent(file=FakeFileUpload(json.dumps(payload).encode()))


ADMIN = SimpleNamespace(id="00000000-0000-0000-0000-000000000000", email="admin@test.local")


@pytest.fixture
def ui_mocks():
    with (
        patch("not_dot_net.frontend.admin_settings.ui") as mock_ui,
        patch("not_dot_net.frontend.admin_settings.log_audit", new_callable=AsyncMock),
        patch("not_dot_net.frontend.admin_settings.t", side_effect=lambda k: k),
    ):
        yield mock_ui


async def test_import_upload_creates_resources(ui_mocks):
    """Upload event must use e.file (not the removed e.content) and persist resources."""
    payload = {
        "version": 1,
        "resources": [
            {"name": "test-pc-1", "resource_type": "desktop", "location": "Lab"},
            {"name": "test-pc-2", "resource_type": "laptop"},
        ],
    }
    await _handle_import_upload(_make_event(payload), replace=False, user=ADMIN)

    names = {r["name"] for r in await export_resources()}
    assert "test-pc-1" in names
    assert "test-pc-2" in names


async def test_import_upload_notifies_on_invalid_json(ui_mocks):
    event = FakeUploadEvent(file=FakeFileUpload(b"not json{{{"))
    await _handle_import_upload(event, replace=False, user=ADMIN)

    ui_mocks.notify.assert_called_once()
    assert "negative" in str(ui_mocks.notify.call_args)


async def test_import_upload_rejects_non_object_json(ui_mocks):
    with patch("not_dot_net.frontend.admin_settings.import_all", new_callable=AsyncMock) as import_all:
        await _handle_import_upload(_make_event(["resources"]), replace=False, user=ADMIN)

    import_all.assert_not_awaited()
    ui_mocks.notify.assert_called_once()
    assert "negative" in str(ui_mocks.notify.call_args)


async def test_import_upload_notifies_on_empty_payload(ui_mocks):
    await _handle_import_upload(_make_event({"version": 1}), replace=False, user=ADMIN)

    ui_mocks.notify.assert_called_once()
    assert "warning" in str(ui_mocks.notify.call_args)


async def test_import_upload_summarizes_missing_result_counters(ui_mocks):
    payload = {"version": 1, "resources": []}
    with patch(
        "not_dot_net.frontend.admin_settings.import_all",
        new_callable=AsyncMock,
        return_value={"resources": {"created": 0, "skipped": 0}},
    ) as import_all:
        await _handle_import_upload(_make_event(payload), replace=True, user=ADMIN)

    import_all.assert_awaited_once_with(payload, replace=True)
    ui_mocks.notify.assert_called_once()
    message = ui_mocks.notify.call_args.args[0]
    assert message == "resources: 0 created, 0 updated, 0 skipped"
    assert ui_mocks.notify.call_args.kwargs["color"] == "positive"
