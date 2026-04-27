import pytest
import uuid
from not_dot_net.backend.workflow_service import (
    create_request,
    submit_step,
    save_draft,
    list_user_requests,
    list_actionable,
    get_request_by_id,
)
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.db import User, get_async_session
from contextlib import asynccontextmanager


async def _create_user(email="staff@test.com", role="staff") -> User:
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password="x",
            role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=["manage_bookings", "manage_roles", "manage_settings",
                     "create_workflows", "approve_workflows", "view_audit_log", "manage_users"],
    )
    cfg.roles["staff"] = RoleDefinition(
        label="Staff",
        permissions=["create_workflows"],
    )
    cfg.roles["director"] = RoleDefinition(
        label="Director",
        permissions=["create_workflows", "approve_workflows"],
    )
    cfg.roles["member"] = RoleDefinition(
        label="Member",
        permissions=[],
    )
    await roles_config.set(cfg)


async def test_create_request():
    user = await _create_user()
    req = await create_request(
        workflow_type="vpn_access",
        created_by=user.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )
    assert req.type == "vpn_access"
    assert req.current_step == "request"
    assert req.status == "in_progress"
    assert req.target_email == "alice@test.com"
    assert req.data["target_name"] == "Alice"


async def test_submit_step_advances():
    await _setup_roles()
    user = await _create_user()
    req = await create_request(
        workflow_type="vpn_access",
        created_by=user.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )
    updated = await submit_step(req.id, user.id, "submit", data={}, actor_user=user)
    assert updated.current_step == "approval"
    assert updated.status == "in_progress"


async def test_approve_completes_workflow():
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    director = await _create_user(email="director@test.com", role="director")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )
    req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    req = await submit_step(req.id, director.id, "approve", data={}, actor_user=director)
    assert req.status == "completed"


async def test_reject_terminates_workflow():
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    director = await _create_user(email="director@test.com", role="director")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )
    req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    req = await submit_step(req.id, director.id, "reject", data={}, comment="Not justified", actor_user=director)
    assert req.status == "rejected"


async def test_save_draft():
    await _setup_roles()
    user = await _create_user()
    req = await create_request(
        workflow_type="onboarding",
        created_by=user.id,
        data={"contact_email": "bob@test.com", "status": "Intern"},
    )
    # Advance to newcomer_info step (generates token for target_person)
    req = await submit_step(req.id, user.id, "submit", data={}, actor_user=user)
    assert req.current_step == "newcomer_info"
    # Save partial data using the token
    req = await save_draft(req.id, data={"phone": "+33 1 23 45"}, actor_token=req.token)
    assert req.data["phone"] == "+33 1 23 45"
    assert req.current_step == "newcomer_info"  # still same step


async def test_list_user_requests():
    user = await _create_user()
    await create_request(
        workflow_type="vpn_access",
        created_by=user.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    await create_request(
        workflow_type="vpn_access",
        created_by=user.id,
        data={"target_name": "B", "target_email": "b@test.com"},
    )
    requests = await list_user_requests(user.id)
    assert len(requests) == 2


async def test_list_actionable_by_role():
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    director = await _create_user(email="director@test.com", role="director")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    # Submit first step to move to approval
    await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    # Director should see it as actionable
    actionable = await list_actionable(director)
    assert len(actionable) == 1
    assert actionable[0].current_step == "approval"


async def test_get_request_by_id():
    user = await _create_user()
    req = await create_request(
        workflow_type="vpn_access",
        created_by=user.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )
    fetched = await get_request_by_id(req.id)
    assert fetched is not None
    assert fetched.id == req.id


async def test_get_request_by_id_not_found():
    fetched = await get_request_by_id(uuid.uuid4())
    assert fetched is None


async def test_token_generated_for_target_person_step():
    """After submitting the onboarding initiation step, a token should be generated for the newcomer_info step."""
    await _setup_roles()
    user = await _create_user()
    req = await create_request(
        workflow_type="onboarding",
        created_by=user.id,
        data={"contact_email": "bob@test.com", "status": "Intern"},
    )
    req = await submit_step(req.id, user.id, "submit", data={}, actor_user=user)
    assert req.current_step == "newcomer_info"
    assert req.token is not None
    assert req.token_expires_at is not None


async def test_token_cleared_on_approval():
    """Token should be cleared after a non-draft action."""
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    director = await _create_user(email="director@test.com", role="director")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )
    req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    req = await submit_step(req.id, director.id, "approve", data={}, actor_user=director)
    assert req.token is None
    assert req.token_expires_at is None


async def test_authorization_check_blocks_wrong_user():
    """submit_step with actor_user should raise PermissionError if user cannot act."""
    await _setup_roles()
    member = await _create_user(email="member@test.com", role="member")
    staff = await _create_user(email="staff@test.com", role="staff")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )
    # member has no create_workflows permission — blocked by submit_step
    with pytest.raises(PermissionError):
        await submit_step(req.id, member.id, "submit", data={}, actor_user=member)


async def test_list_actionable_returns_only_in_progress():
    """Completed requests should not appear in actionable list."""
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    director = await _create_user(email="director@test.com", role="director")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    req = await submit_step(req.id, director.id, "approve", data={}, actor_user=director)
    assert req.status == "completed"

    actionable = await list_actionable(director)
    assert len(actionable) == 0


async def test_create_request_requires_permission():
    await _setup_roles()
    member = await _create_user(email="member@test.com", role="member")
    with pytest.raises(PermissionError):
        await create_request(
            workflow_type="vpn_access",
            created_by=member.id,
            data={"target_name": "A", "target_email": "a@test.com"},
            actor=member,
        )


async def test_create_request_allowed_with_permission():
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
        actor=staff,
    )
    assert req.type == "vpn_access"


async def test_onboarding_v2_full_flow():
    """Test the complete 4-step onboarding: initiation → newcomer_info → admin_validation → it_account_creation."""
    await _setup_roles()
    cfg = await roles_config.get()
    cfg.roles["admin"].permissions.append("access_personal_data")
    cfg.roles["admin"].permissions.append("manage_users")
    await roles_config.set(cfg)

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

    # Step 2: Newcomer submits info via token
    req = await submit_step(
        req.id, actor_id=None, action="submit",
        data={"first_name": "Marie", "last_name": "Curie", "phone": "+33 1 00 00"},
        actor_token=req.token,
    )
    assert req.current_step == "admin_validation"

    # Step 3: Admin approves
    req = await submit_step(req.id, admin.id, "approve", data={}, actor_user=admin)
    assert req.current_step == "it_account_creation"

    # Step 4: IT marks complete
    req = await submit_step(req.id, admin.id, "complete", data={"notes": "account: mcurie"}, actor_user=admin)
    assert req.status == "completed"


async def test_onboarding_v2_request_corrections():
    """Admin sends workflow back to newcomer_info via request_corrections."""
    await _setup_roles()
    cfg = await roles_config.get()
    cfg.roles["admin"].permissions.append("access_personal_data")
    await roles_config.set(cfg)

    initiator = await _create_user(email="initiator@test.com", role="staff")
    admin = await _create_user(email="admin@test.com", role="admin")

    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "newcomer@example.com", "status": "CDD"},
        actor=initiator,
    )
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)
    req = await submit_step(
        req.id, actor_id=None, action="submit",
        data={"first_name": "Jean", "last_name": "Dupont"},
        actor_token=req.token,
    )
    assert req.current_step == "admin_validation"

    # Admin requests corrections
    req = await submit_step(
        req.id, admin.id, "request_corrections",
        comment="Please re-upload ID document",
        actor_user=admin,
    )
    assert req.current_step == "newcomer_info"
    assert req.status == "in_progress"
    assert req.token is not None  # new token generated for target_person step
