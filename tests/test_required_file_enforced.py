"""Reproducer: required file fields must block submit when nothing is uploaded.

Bug (workflow_step.validated_submit): the required-field check excluded file
fields entirely (`and f.type != "file"`), so a newcomer could submit a step
whose required document uploads were never provided. Result: the request
advanced with zero WorkflowFile rows, and reviewers saw no documents at all.
"""
import uuid
from datetime import datetime, timedelta, timezone

from nicegui.testing import User
from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowRequest
from not_dot_net.backend.workflow_config import WorkflowsConfig, workflows_config
from not_dot_net.config import FieldConfig, WorkflowConfig, WorkflowStepConfig


async def test_required_file_blocks_submit_when_missing(user: User, monkeypatch):
    import not_dot_net.frontend.workflow_token as wt_mod

    await workflows_config.set(WorkflowsConfig(workflows={
        "doc_wf": WorkflowConfig(label="Docs", steps=[
            WorkflowStepConfig(
                key="docs", type="form", assignee="target_person",
                fields=[FieldConfig(name="id_document", type="file",
                                    required=True, label="id_document")],
                actions=["submit"],
            ),
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

    async def _true(*_a, **_kw):
        return True

    async def _false(*_a, **_kw):
        return False

    monkeypatch.setattr(wt_mod, "is_locked_out", _false)
    monkeypatch.setattr(wt_mod, "has_valid_code", _true)
    monkeypatch.setattr(wt_mod, "verify_code", _true)

    await user.open(f"/workflow/token/{tok}")
    user.find("Verify").click()
    await user.should_see("ID Document")

    # Click Submit without uploading the required file.
    user.find("Submit").click()
    await user.should_see("This field is required")

    # The request must NOT have advanced past the file step.
    async with session_scope() as s:
        req = (await s.execute(
            select(WorkflowRequest).where(WorkflowRequest.id == req_id)
        )).scalar_one()
        assert req.current_step == "docs", (
            f"submit advanced to {req.current_step!r} despite missing required file"
        )
