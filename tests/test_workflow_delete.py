"""Superuser hard-delete of workflow requests (test data cleanup)."""

from not_dot_net.backend.permissions import SYSTEM_ACTOR
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from not_dot_net.backend.audit import AuditEvent
from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.encrypted_storage import EncryptedFile
from not_dot_net.backend.workflow_models import WorkflowEvent, WorkflowFile, WorkflowRequest
from not_dot_net.backend.workflow_service import create_request, delete_request


async def _create_user(email="staff@test.com", is_superuser=False) -> User:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password="x",
            role="staff",
            is_superuser=is_superuser,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _make_request(creator: User) -> WorkflowRequest:
    return await create_request(
        workflow_type="vpn_access",
        created_by=creator.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"}, actor=SYSTEM_ACTOR
    )


async def _attach_files(req_id: uuid.UUID, upload_root) -> uuid.UUID:
    """Attach one plain upload (on disk) and one encrypted file. Returns enc file id."""
    upload_dir = upload_root / str(req_id)
    upload_dir.mkdir(parents=True)
    plain_path = upload_dir / "doc.pdf"
    plain_path.write_bytes(b"plain")

    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        enc_file = EncryptedFile(
            wrapped_dek=b"wrapped",
            nonce=b"nonce",
            storage_path="data/encrypted/test-delete.enc",
            original_filename="id.pdf",
            content_type="application/pdf",
        )
        session.add(enc_file)
        await session.flush()
        session.add(WorkflowFile(
            request_id=req_id,
            step_key="request",
            field_name="doc",
            filename="doc.pdf",
            storage_path=str(plain_path),
        ))
        session.add(WorkflowFile(
            request_id=req_id,
            step_key="request",
            field_name="id_document",
            filename="id.pdf",
            storage_path="encrypted",
            encrypted_file_id=enc_file.id,
        ))
        await session.commit()
        return enc_file.id


async def test_delete_request_requires_superuser():
    user = await _create_user()
    req = await _make_request(user)

    with pytest.raises(PermissionError):
        await delete_request(req.id, actor_user=user)

    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        assert await session.get(WorkflowRequest, req.id) is not None


async def test_delete_request_unknown_id():
    admin = await _create_user(email="root@test.com", is_superuser=True)
    with pytest.raises(ValueError):
        await delete_request(uuid.uuid4(), actor_user=admin)


async def test_delete_request_removes_everything(tmp_path, monkeypatch):
    import not_dot_net.backend.workflow_uploads as wu
    monkeypatch.setattr(wu, "UPLOAD_ROOT", tmp_path)

    user = await _create_user()
    admin = await _create_user(email="root@test.com", is_superuser=True)
    req = await _make_request(user)
    enc_id = await _attach_files(req.id, tmp_path)

    await delete_request(req.id, actor_user=admin)

    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        assert await session.get(WorkflowRequest, req.id) is None
        events = (await session.execute(
            select(WorkflowEvent).where(WorkflowEvent.request_id == req.id)
        )).scalars().all()
        assert events == []
        files = (await session.execute(
            select(WorkflowFile).where(WorkflowFile.request_id == req.id)
        )).scalars().all()
        assert files == []
        assert await session.get(EncryptedFile, enc_id) is None

    assert not (tmp_path / str(req.id)).exists()


async def test_delete_request_writes_audit_entry():
    user = await _create_user()
    admin = await _create_user(email="root@test.com", is_superuser=True)
    req = await _make_request(user)

    await delete_request(req.id, actor_user=admin)

    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        rows = (await session.execute(
            select(AuditEvent).where(
                AuditEvent.category == "workflow",
                AuditEvent.action == "delete",
                AuditEvent.target_id == str(req.id),
            )
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].actor_id == str(admin.id)
        assert "vpn_access" in (rows[0].detail or "")


async def test_delete_request_works_on_completed_request():
    user = await _create_user()
    admin = await _create_user(email="root@test.com", is_superuser=True)
    req = await _make_request(user)

    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        db_req = await session.get(WorkflowRequest, req.id)
        db_req.status = "completed"
        await session.commit()

    await delete_request(req.id, actor_user=admin)

    async with get_session() as session:
        assert await session.get(WorkflowRequest, req.id) is None
