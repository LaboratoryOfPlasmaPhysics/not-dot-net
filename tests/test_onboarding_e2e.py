"""End-to-end test for the 4-step onboarding workflow with encrypted files."""

import pytest
import uuid
from contextlib import asynccontextmanager

from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.workflow_service import (
    create_request, submit_step, save_draft, get_request_by_token, list_actionable,
)
from not_dot_net.backend.encrypted_storage import store_encrypted, read_encrypted, EncryptedFile
from not_dot_net.backend.verification import generate_verification_code, verify_code
from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowFile


async def _create_user(email="staff@test.com", role="staff") -> User:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=["manage_bookings", "manage_roles", "manage_settings",
                     "create_workflows", "approve_workflows", "view_audit_log",
                     "manage_users", "access_personal_data"],
    )
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=["create_workflows"])
    await roles_config.set(cfg)


@pytest.mark.asyncio
async def test_full_onboarding_with_encrypted_files():
    await _setup_roles()
    initiator = await _create_user(email="initiator@test.com", role="staff")
    admin = await _create_user(email="admin@test.com", role="admin")

    # Step 1: Initiation
    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "newcomer@example.com", "status": "PhD"},
        actor=initiator,
    )
    assert req.current_step == "initiation"
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)
    assert req.current_step == "newcomer_info"
    assert req.token is not None
    found_by_token = await get_request_by_token(req.token)
    assert found_by_token is not None
    assert found_by_token.id == req.id

    # Step 2: Verification code
    code = await generate_verification_code(req.id)
    assert len(code) == 6
    assert await verify_code(req.id, code) is True

    # Step 2: Upload encrypted file
    enc_file = await store_encrypted(b"fake ID document", "id.pdf", "application/pdf", None)
    async with session_scope() as session:
        wf_file = WorkflowFile(
            request_id=req.id,
            step_key="newcomer_info",
            field_name="id_document",
            filename="id.pdf",
            storage_path="",
            encrypted_file_id=enc_file.id,
        )
        session.add(wf_file)
        await session.commit()

    # Step 2: Submit newcomer info
    req = await submit_step(
        req.id, actor_id=None, action="submit",
        data={"first_name": "Marie", "last_name": "Curie", "phone": "+33 1 00 00"},
        actor_token=req.token,
    )
    assert req.current_step == "admin_validation"

    actionable = await list_actionable(admin)
    assert any(item.id == req.id for item in actionable)

    # Step 3: Admin can read encrypted file
    data, name, ctype = await read_encrypted(enc_file.id, actor_id=admin.id, actor_email=admin.email)
    assert data == b"fake ID document"

    # Step 3: Admin approves
    req = await submit_step(req.id, admin.id, "approve", data={}, actor_user=admin)
    assert req.current_step == "it_account_creation"

    # Step 4: IT completes
    req = await submit_step(req.id, admin.id, "complete", data={"notes": "account: mcurie"}, actor_user=admin)
    assert req.status == "completed"

    async with session_scope() as session:
        retained_file = await session.get(EncryptedFile, enc_file.id)
        assert retained_file.retained_until is not None


@pytest.mark.asyncio
async def test_request_corrections_regenerates_token():
    await _setup_roles()
    initiator = await _create_user(email="init@test.com", role="staff")
    admin = await _create_user(email="adm@test.com", role="admin")

    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "new@example.com", "status": "CDD"},
        actor=initiator,
    )
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)
    first_token = req.token

    req = await submit_step(
        req.id, actor_id=None, action="submit",
        data={"first_name": "Jean", "last_name": "Dupont"},
        actor_token=req.token,
    )
    assert req.current_step == "admin_validation"

    req = await submit_step(
        req.id, admin.id, "request_corrections",
        comment="Missing ID",
        actor_user=admin,
    )
    assert req.current_step == "newcomer_info"
    assert req.token is not None
    assert req.token != first_token


@pytest.mark.asyncio
async def test_save_draft_preserves_data():
    await _setup_roles()
    initiator = await _create_user(email="init2@test.com", role="staff")

    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "partial@example.com", "status": "Intern"},
        actor=initiator,
    )
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)

    req = await save_draft(req.id, data={"first_name": "Partial"}, actor_token=req.token)
    assert req.data["first_name"] == "Partial"
    assert req.current_step == "newcomer_info"
