"""Plain workflow uploads must use a unique path per upload so versions and
same-named files in different fields never overwrite each other on disk."""
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowRequest
import not_dot_net.backend.workflow_uploads as wu
import not_dot_net.backend.workflow_service as ws


async def _make_request() -> uuid.UUID:
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    async with session_scope() as s:
        row = WorkflowRequest(type="doc_wf", current_step="docs",
                              token=str(uuid.uuid4()), token_expires_at=expiry)
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return row.id


async def test_plain_reupload_keeps_both_versions_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(wu, "UPLOAD_ROOT", tmp_path)
    rid = await _make_request()

    v1 = await wu.persist_workflow_upload(
        request_id=rid, step_key="docs", field_name="doc",
        content=b"VERSION-1", filename="report.pdf",
        content_type="application/pdf", encrypted=False, uploaded_by=None,
    )
    v2 = await wu.persist_workflow_upload(
        request_id=rid, step_key="docs", field_name="doc",
        content=b"VERSION-2", filename="report.pdf",
        content_type="application/pdf", encrypted=False, uploaded_by=None,
    )

    assert v1.storage_path != v2.storage_path
    assert Path(v1.storage_path).read_bytes() == b"VERSION-1"
    assert Path(v2.storage_path).read_bytes() == b"VERSION-2"
    assert Path(v1.storage_path).is_relative_to(tmp_path / str(rid))


async def test_plain_same_filename_different_fields_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(wu, "UPLOAD_ROOT", tmp_path)
    rid = await _make_request()

    a = await wu.persist_workflow_upload(
        request_id=rid, step_key="docs", field_name="invitation",
        content=b"AAA", filename="report.pdf",
        content_type="application/pdf", encrypted=False, uploaded_by=None,
    )
    b = await wu.persist_workflow_upload(
        request_id=rid, step_key="docs", field_name="budget",
        content=b"BBB", filename="report.pdf",
        content_type="application/pdf", encrypted=False, uploaded_by=None,
    )

    assert a.storage_path != b.storage_path
    assert Path(a.storage_path).read_bytes() == b"AAA"
    assert Path(b.storage_path).read_bytes() == b"BBB"
