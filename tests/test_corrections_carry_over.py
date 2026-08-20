"""A file already uploaded must carry over on corrections: shown as present,
submittable without re-upload, and replaceable via the Replace button."""
import uuid
from datetime import datetime, timedelta, timezone

from nicegui.testing import User
from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowFile, WorkflowRequest
from not_dot_net.backend.workflow_config import WorkflowsConfig, workflows_config
from not_dot_net.config import FieldConfig, WorkflowConfig, WorkflowStepConfig


async def _doc_wf_with_existing_file(monkeypatch, filename="ALREADY.png"):
    """A one-required-file workflow sitting at the token step with `filename`
    already uploaded; verification-code gate bypassed. Returns (token, req_id)."""
    import not_dot_net.frontend.workflow_token as wt_mod

    await workflows_config.set(WorkflowsConfig(workflows={
        "doc_wf": WorkflowConfig(label="Docs", steps=[
            WorkflowStepConfig(
                key="docs", type="form", assignee="target_person",
                fields=[FieldConfig(name="id_document", type="file",
                                    required=True, label="id_document")],
                actions=["submit"]),
            WorkflowStepConfig(key="done", type="form",
                               assignee="target_person", actions=["submit"]),
        ]),
    }))

    tok = str(uuid.uuid4())
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    async with session_scope() as s:
        row = WorkflowRequest(type="doc_wf", current_step="docs",
                              token=tok, token_expires_at=expiry)
        s.add(row)
        await s.commit()
        await s.refresh(row)
        req_id = row.id
        s.add(WorkflowFile(request_id=req_id, step_key="docs",
                           field_name="id_document", filename=filename,
                           storage_path=f"data/uploads/x/{filename}"))
        await s.commit()

    async def _true(*_a, **_kw):
        return True

    async def _false(*_a, **_kw):
        return False

    monkeypatch.setattr(wt_mod, "is_locked_out", _false)
    monkeypatch.setattr(wt_mod, "has_valid_code", _true)
    monkeypatch.setattr(wt_mod, "verify_code", _true)
    return tok, req_id


async def test_existing_file_carries_over_and_submits(user: User, monkeypatch):
    tok, req_id = await _doc_wf_with_existing_file(monkeypatch)

    await user.open(f"/workflow/token/{tok}")
    user.find("Verify").click()
    await user.should_see("ALREADY.png")  # shown as already uploaded

    user.find("Submit").click()
    await user.should_see("Step submitted")  # no re-upload required

    async with session_scope() as s:
        req = (await s.execute(
            select(WorkflowRequest).where(WorkflowRequest.id == req_id)
        )).scalar_one()
        assert req.current_step == "done"


async def test_replace_reveals_upload_widget(user: User, monkeypatch):
    """Clicking Replace on a carried-over file swaps the present-file display
    for the upload widget so a new version can be picked."""
    tok, _ = await _doc_wf_with_existing_file(monkeypatch)

    await user.open(f"/workflow/token/{tok}")
    user.find("Verify").click()
    await user.should_see("ALREADY.png")  # carried-over file shown first

    user.find("Replace").click()
    await user.should_not_see("ALREADY.png")  # swapped to the upload widget
