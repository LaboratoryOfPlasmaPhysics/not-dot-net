import uuid

import pytest
from sqlalchemy import select

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.workflow_models import WorkflowFile, WorkflowRequest
from not_dot_net.frontend import new_request
from not_dot_net.config import FieldConfig, WorkflowStepConfig


async def test_new_request_persists_staged_plain_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(new_request, "UPLOAD_ROOT", tmp_path)

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
    assert (tmp_path / str(request_id) / "program.pdf").read_bytes() == b"%PDF fake program"


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
