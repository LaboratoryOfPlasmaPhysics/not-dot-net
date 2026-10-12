"""Form steps on the logged-in detail page get uploads, existing files and drafts.

Review 2026-10-12 W3: the detail page called render_step_form without files or
an upload handler, so a required file field could never be satisfied there —
e.g. a returning person (still an active user) acting on their own
`newcomer_info` step from the dashboard.
"""

import pytest
from contextlib import asynccontextmanager
from types import SimpleNamespace

from nicegui.testing import User as UiUser
from sqlalchemy import select

from not_dot_net.backend.db import session_scope, get_user_db
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.backend.users import get_user_manager, get_jwt_strategy
from not_dot_net.backend.workflow_models import WorkflowFile
from not_dot_net.backend.workflow_service import create_request, submit_step
from not_dot_net.frontend.step_uploads import make_upload_handler
from tests.test_workflow_detail_download import _make_admin, _open_as


async def _create_plain_user(email: str):
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                return await manager.create(UserCreate(email=email, password="pw123456"))


async def _request_at_newcomer_info(admin):
    req = await create_request(
        workflow_type="onboarding",
        created_by=admin.id,
        data={"contact_email": "newcomer@example.com", "status": "PhD"},
        actor=admin,
    )
    req = await submit_step(req.id, admin.id, "submit", data={}, actor_user=admin)
    assert req.current_step == "newcomer_info"
    return req


async def test_target_person_gets_upload_and_draft_on_detail_page(user: UiUser):
    admin = await _make_admin("admin-w3@test.com")
    req = await _request_at_newcomer_info(admin)
    newcomer = await _create_plain_user("newcomer@example.com")

    await _open_as(user, newcomer, f"/workflow/request/{req.id}")
    await user.should_see("Save Draft")
    await user.should_not_see("no upload available")


class _FakeUpload:
    name = "id.pdf"
    content_type = "application/pdf"

    def size(self) -> int:
        return len(b"%PDF-1.4 fake")

    async def read(self):
        return b"%PDF-1.4 fake"


@pytest.fixture
def notified(monkeypatch) -> list[str]:
    """Capture ui.notify messages — the handler runs outside a page slot here."""
    from not_dot_net.frontend import step_uploads
    messages: list[str] = []
    monkeypatch.setattr(step_uploads.ui, "notify", lambda msg, **_: messages.append(msg))
    return messages


async def _files_for(request_id) -> list[WorkflowFile]:
    async with session_scope() as session:
        rows = await session.execute(select(WorkflowFile).where(WorkflowFile.request_id == request_id))
        return list(rows.scalars())


async def test_upload_handler_refuses_when_no_longer_authorized(user: UiUser, notified):
    admin = await _make_admin("admin-w3b@test.com")
    req = await _request_at_newcomer_info(admin)
    step = SimpleNamespace(key="newcomer_info")

    async def denied() -> bool:
        return False

    _, handle = await make_upload_handler(
        req.id, step, encrypted_fields=set(), uploaded_by=None,
        authorize=denied, denied_message="access_denied",
    )
    await handle("id_document", SimpleNamespace(file=_FakeUpload()))
    assert await _files_for(req.id) == []
    assert notified == ["Access denied"]


async def test_upload_handler_persists_when_authorized(user: UiUser, tmp_path, monkeypatch, notified):
    from not_dot_net.backend import workflow_uploads
    monkeypatch.setattr(workflow_uploads, "UPLOAD_ROOT", tmp_path)
    admin = await _make_admin("admin-w3c@test.com")
    req = await _request_at_newcomer_info(admin)
    step = SimpleNamespace(key="newcomer_info")

    async def allowed() -> bool:
        return True

    uploaded, handle = await make_upload_handler(
        req.id, step, encrypted_fields=set(), uploaded_by=admin.id,
        authorize=allowed, denied_message="access_denied",
    )
    await handle("id_document", SimpleNamespace(file=_FakeUpload()))
    files = await _files_for(req.id)
    assert [f.filename for f in files] == ["id.pdf"]
    assert uploaded == {"id_document": "id.pdf"}


class _HugeUpload:
    """Reports 2 GB and refuses to be read: the size check must come first."""
    name = "big.pdf"
    content_type = "application/pdf"

    def size(self) -> int:
        return 2 * 1024 ** 3

    async def read(self):
        raise AssertionError("an oversized upload must be rejected before it is read")


async def test_upload_handler_rejects_oversized_file_before_reading(user: UiUser, notified):
    """Review 2026-10-12 S7: max_file_size is only a Quasar prop; the server
    read the whole body into memory before checking its length."""
    admin = await _make_admin("admin-s7@test.com")
    req = await _request_at_newcomer_info(admin)

    async def allowed() -> bool:
        return True

    _, handle = await make_upload_handler(
        req.id, SimpleNamespace(key="newcomer_info"), encrypted_fields=set(), uploaded_by=None,
        authorize=allowed, denied_message="token_expired",
    )
    await handle("id_document", SimpleNamespace(file=_HugeUpload()))
    assert await _files_for(req.id) == []
    assert len(notified) == 1 and "too large" in notified[0].lower()
