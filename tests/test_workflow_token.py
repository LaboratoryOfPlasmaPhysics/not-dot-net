"""Reproducer: token page must route FieldRef-encrypted file uploads through store_encrypted."""
import uuid
from datetime import datetime, timedelta, timezone

from nicegui import ui
from nicegui.testing import User
from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.field_definitions import (
    FieldDefinition,
    FieldDefinitionsConfig,
    field_definitions_config,
)
from not_dot_net.backend.workflow_models import WorkflowFile, WorkflowRequest
from not_dot_net.backend.workflow_config import WorkflowsConfig, workflows_config
from not_dot_net.config import FieldRef, WorkflowConfig, WorkflowStepConfig


async def test_token_page_shows_expired_for_unknown_token(user: User):
    """B-T4: opening the token page with an unknown/expired token must render
    the expired-link message, not crash or leak."""
    from not_dot_net.frontend.i18n import t

    await user.open("/workflow/token/00000000-0000-0000-0000-000000000000")
    await user.should_see(t("token_expired"))


async def test_fieldref_encrypted_file_stored_encrypted(user: User, monkeypatch):
    """FieldRef to encrypted definition must route uploads through store_encrypted.

    Bug (workflow_token.py ~L110): encrypted_fields was built from raw step.fields.
    FieldRef.encrypted is None (falsy) even when the definition has encrypted=True,
    so the field is silently excluded from encrypted_fields → file stored CLEARTEXT.

    Fix: use await resolve_step_fields(step) which merges the definition's encrypted flag.
    """
    import not_dot_net.frontend.workflow_token as wt_mod

    # Setup: field definition with encrypted=True, workflow using a FieldRef
    await field_definitions_config.set(FieldDefinitionsConfig(definitions={
        "passport": FieldDefinition(key="passport", type="file", encrypted=True),
    }))
    await workflows_config.set(WorkflowsConfig(workflows={
        "doc_wf": WorkflowConfig(label="Docs", steps=[
            WorkflowStepConfig(
                key="docs", type="form", assignee="target_person",
                fields=[FieldRef(ref="passport")], actions=["submit"],
            ),
        ]),
    }))

    # Create a WorkflowRequest directly at the token step (bypass the full workflow flow)
    tok = str(uuid.uuid4())
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    req_id: uuid.UUID | None = None
    async with session_scope() as s:
        row = WorkflowRequest(type="doc_wf", current_step="docs",
                               token=tok, token_expires_at=expiry)
        s.add(row)
        await s.commit()
        await s.refresh(row)
        req_id = row.id

    # Bypass the verification-code gate
    async def _true(*_a, **_kw):
        return True

    async def _false(*_a, **_kw):
        return False

    monkeypatch.setattr(wt_mod, "is_locked_out", _false)
    monkeypatch.setattr(wt_mod, "has_valid_code", _true)
    monkeypatch.setattr(wt_mod, "verify_code", _true)
    monkeypatch.setattr(wt_mod, "send_mail", _true)
    monkeypatch.setattr(wt_mod, "validate_upload", lambda *a, **kw: None)

    # Capture the on_file_upload handler that _render_form passes to render_step_form
    captured: dict = {}

    async def _capturing_render(step, data, *, on_submit, on_save_draft=None,
                                 files=None, on_file_upload=None,
                                 max_upload_size_mb=10):
        captured["on_file_upload"] = on_file_upload
        captured["req_id"] = req_id
        ui.label("FORM_RENDERED")  # sentinel so should_see can wait

    monkeypatch.setattr(wt_mod, "render_step_form", _capturing_render)

    # Open the token page; has_valid_code=True shows the code-input form immediately
    await user.open(f"/workflow/token/{tok}")
    # Click Verify → verify_code returns True → _render_form is called → render_step_form captured
    user.find("Verify").click()
    await user.should_see("FORM_RENDERED")

    assert "on_file_upload" in captured, (
        "render_step_form was not called; _render_form may not have been triggered"
    )

    # Simulate an upload event for the "passport" field
    class _MockFile:
        name = "passport.pdf"
        content_type = "application/pdf"

        async def read(self):
            return b"fake content"

    class _MockEvent:
        file = _MockFile()

    await captured["on_file_upload"]("passport", _MockEvent())

    # Assert: the file was stored ENCRYPTED (encrypted_file_id must be set)
    async with session_scope() as s:
        rows = (await s.execute(
            select(WorkflowFile).where(WorkflowFile.request_id == captured["req_id"])
        )).scalars().all()

    assert len(rows) == 1, f"Expected 1 WorkflowFile, got {len(rows)}"
    assert rows[0].encrypted_file_id is not None, (
        "File was stored CLEARTEXT — FieldRef.encrypted=None was not resolved from its definition. "
        "Fix: use resolve_step_fields() in workflow_token.py."
    )


async def test_stale_tab_cannot_upload_after_token_cleared(user: User, monkeypatch):
    """Uploads must re-validate the token server-side: a tab left open while the
    request advances past target_person (token cleared) or completes must not be
    able to attach new files — they would displace the reviewed 'current' file."""
    import not_dot_net.frontend.workflow_token as wt_mod

    await field_definitions_config.set(FieldDefinitionsConfig(definitions={
        "passport": FieldDefinition(key="passport", type="file", encrypted=False),
    }))
    await workflows_config.set(WorkflowsConfig(workflows={
        "doc_wf": WorkflowConfig(label="Docs", steps=[
            WorkflowStepConfig(
                key="docs", type="form", assignee="target_person",
                fields=[FieldRef(ref="passport")], actions=["submit"],
            ),
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
    monkeypatch.setattr(wt_mod, "send_mail", _true)
    monkeypatch.setattr(wt_mod, "validate_upload", lambda *a, **kw: None)

    captured: dict = {}

    async def _capturing_render(step, data, *, on_submit, on_save_draft=None,
                                files=None, on_file_upload=None,
                                max_upload_size_mb=10):
        captured["on_file_upload"] = on_file_upload
        ui.label("FORM_RENDERED")

    monkeypatch.setattr(wt_mod, "render_step_form", _capturing_render)

    await user.open(f"/workflow/token/{tok}")
    user.find("Verify").click()
    await user.should_see("FORM_RENDERED")

    # The request moves on: token cleared (what every non-draft action does).
    async with session_scope() as s:
        row = await s.get(WorkflowRequest, req_id)
        row.token = None
        row.token_expires_at = None
        await s.commit()

    class _MockFile:
        name = "passport.pdf"
        content_type = "application/pdf"

        async def read(self):
            return b"%PDF fake"

    class _MockEvent:
        file = _MockFile()

    await captured["on_file_upload"]("passport", _MockEvent())

    async with session_scope() as s:
        rows = (await s.execute(
            select(WorkflowFile).where(WorkflowFile.request_id == req_id)
        )).scalars().all()
    assert rows == [], "Stale tab uploaded into a request whose token was cleared"
