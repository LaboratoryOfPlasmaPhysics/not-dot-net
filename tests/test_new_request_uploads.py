import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.encrypted_storage import EncryptedFile
from not_dot_net.backend import workflow_service
from not_dot_net.backend.workflow_models import WorkflowFile, WorkflowRequest
from not_dot_net.frontend import new_request
from not_dot_net.config import FieldConfig, WorkflowStepConfig


async def test_new_request_persists_staged_plain_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(new_request, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(workflow_service, "UPLOAD_ROOT", tmp_path)

    user_id = uuid.uuid4()
    request_id = uuid.uuid4()
    step = WorkflowStepConfig(
        key="submission",
        type="form",
        fields=[
            FieldConfig(
                name="invitation_or_program",
                type="file",
                label="Invitation or program",
            )
        ],
    )

    async with session_scope() as session:
        session.add(User(id=user_id, email="mission@test.com", hashed_password="x"))
        session.add(
            WorkflowRequest(
                id=request_id,
                type="ordre_de_mission",
                current_step="submission",
                created_by=user_id,
            )
        )
        await session.commit()

    await new_request._persist_staged_uploads(
        request_id,
        step,
        {
            "invitation_or_program": (
                b"%PDF fake program",
                "program.pdf",
                "application/pdf",
            )
        },
        user_id,
    )

    async with session_scope() as session:
        row = (
            await session.execute(
                select(WorkflowFile).where(WorkflowFile.request_id == request_id)
            )
        ).scalar_one()

    assert row.step_key == "submission"
    assert row.field_name == "invitation_or_program"
    assert row.filename == "program.pdf"
    assert row.uploaded_by == user_id
    assert row.encrypted_file_id is None
    stored_path = Path(row.storage_path)
    assert stored_path.is_relative_to(tmp_path / str(request_id))
    assert stored_path.read_bytes() == b"%PDF fake program"


async def test_new_request_plain_uploads_are_namespaced_by_field(tmp_path, monkeypatch):
    monkeypatch.setattr(new_request, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(workflow_service, "UPLOAD_ROOT", tmp_path)

    user_id = uuid.uuid4()
    request_id = uuid.uuid4()
    step = WorkflowStepConfig(
        key="submission",
        type="form",
        fields=[
            FieldConfig(name="invitation", type="file", label="Invitation"),
            FieldConfig(name="program", type="file", label="Program"),
        ],
    )

    async with session_scope() as session:
        session.add(User(id=user_id, email="mission-collision@test.com", hashed_password="x"))
        session.add(
            WorkflowRequest(
                id=request_id,
                type="ordre_de_mission",
                current_step="submission",
                created_by=user_id,
            )
        )
        await session.commit()

    await new_request._persist_staged_uploads(
        request_id,
        step,
        {
            "invitation": (b"%PDF invitation", "report.pdf", "application/pdf"),
            "program": (b"%PDF program", "report.pdf", "application/pdf"),
        },
        user_id,
    )

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(WorkflowFile).where(WorkflowFile.request_id == request_id)
            )
        ).scalars().all()

    paths = {row.field_name: row.storage_path for row in rows}
    assert paths["invitation"] != paths["program"]
    assert Path(paths["invitation"]).read_bytes() == b"%PDF invitation"
    assert Path(paths["program"]).read_bytes() == b"%PDF program"
    assert Path(paths["invitation"]).is_relative_to(tmp_path / str(request_id))
    assert Path(paths["program"]).is_relative_to(tmp_path / str(request_id))


async def test_failed_request_cleanup_removes_encrypted_upload_blobs():
    user_id = uuid.uuid4()
    request_id = uuid.uuid4()
    step = WorkflowStepConfig(
        key="newcomer_info",
        type="form",
        fields=[
            FieldConfig(
                name="id_document",
                type="file",
                label="ID document",
                encrypted=True,
            )
        ],
    )

    async with session_scope() as session:
        session.add(User(id=user_id, email="mission-encrypted@test.com", hashed_password="x"))
        session.add(
            WorkflowRequest(
                id=request_id,
                type="onboarding",
                current_step="newcomer_info",
                created_by=user_id,
            )
        )
        await session.commit()

    await new_request._persist_staged_uploads(
        request_id,
        step,
        {"id_document": (b"%PDF secret", "id.pdf", "application/pdf")},
        user_id,
    )

    async with session_scope() as session:
        wf_file = (
            await session.execute(
                select(WorkflowFile).where(WorkflowFile.request_id == request_id)
            )
        ).scalar_one()
        enc_file = await session.get(EncryptedFile, wf_file.encrypted_file_id)
        blob_path = enc_file.storage_path

    await new_request._discard_failed_request(request_id)

    async with session_scope() as session:
        assert await session.get(WorkflowRequest, request_id) is None
        assert await session.get(EncryptedFile, wf_file.encrypted_file_id) is None

    assert not Path(blob_path).exists()


async def test_new_request_cleans_created_request_when_upload_persist_fails(monkeypatch):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email="mission-fail@test.com",
        hashed_password="x",
        is_superuser=True,
    )
    step = WorkflowStepConfig(key="request", type="form")

    async with session_scope() as session:
        session.add(user)
        await session.commit()

    async def fail_persist(*_args, **_kwargs):
        raise OSError("upload storage unavailable")

    monkeypatch.setattr(new_request, "_persist_staged_uploads", fail_persist)

    with pytest.raises(OSError, match="upload storage unavailable"):
        await new_request._create_and_submit_request(
            user,
            "vpn_access",
            step,
            {"target_name": "Alice", "target_email": "alice@test.com"},
            {"attachment": (b"%PDF fake", "program.pdf", "application/pdf")},
        )

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(WorkflowRequest).where(WorkflowRequest.created_by == user_id)
            )
        ).scalars().all()

    assert rows == []


async def test_submit_post_commit_audit_failure_keeps_submitted_request(monkeypatch):
    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email="audit-fail@test.com",
        hashed_password="x",
        is_superuser=True,
    )
    async with session_scope() as session:
        session.add(user)
        await session.commit()

    req = await workflow_service.create_request(
        workflow_type="vpn_access",
        created_by=user.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
        actor=user,
    )

    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    import not_dot_net.backend.audit as audit_module
    monkeypatch.setattr(audit_module, "log_audit", fail_audit)

    updated = await workflow_service.submit_step(
        req.id,
        user.id,
        "submit",
        data={},
        actor_user=user,
    )

    assert updated.current_step == "approval"
    async with session_scope() as session:
        stored = await session.get(WorkflowRequest, req.id)
    assert stored is not None
    assert stored.current_step == "approval"
