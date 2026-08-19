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
from not_dot_net.backend.encrypted_storage import EncryptedFile
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.workflow_models import WorkflowFile
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


async def test_submit_step_survives_post_commit_session_error(monkeypatch):
    """A post-commit session failure must not abort a durably-committed transition.

    submit_step commits the step transition, then previously ran an unguarded
    session.refresh(req). Its caller (_create_and_submit_request) discards the
    request on ANY exception — so a post-commit error would delete a legitimately
    submitted request plus its files/blobs. The transition is already durable;
    a late session hiccup must not turn into a lost request.
    """
    await _setup_roles()
    user = await _create_user()
    req = await create_request(
        workflow_type="vpn_access",
        created_by=user.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )

    from sqlalchemy.ext.asyncio import AsyncSession

    async def boom(self, *a, **k):
        raise RuntimeError("connection dropped after commit")

    monkeypatch.setattr(AsyncSession, "refresh", boom)

    updated = await submit_step(req.id, user.id, "submit", data={}, actor_user=user)
    assert updated.current_step == "approval"

    monkeypatch.undo()
    reloaded = await get_request_by_id(req.id)
    assert reloaded.current_step == "approval"
    assert reloaded.status == "in_progress"


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
    req = await submit_step(req.id, director.id, "approve", data={}, actor_user=director, ad_creds=("admin", "pass"))
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


async def test_list_actionable_requester_step_hidden_from_other_users():
    """A step assigned to the requester must never appear in another user's
    actionable list, even when the step also carries an assignee_permission
    that the other user holds (everyone has create_workflows — the dashboard
    was showing everyone's requests stuck at requester steps)."""
    await _setup_roles()
    alice = await _create_user(email="alice@test.com", role="staff")
    bob = await _create_user(email="bob@test.com", role="staff")
    req = await create_request(
        workflow_type="ordre_de_mission",
        created_by=alice.id,
        data={"mission_subject": "conf"},
        actor=alice,
    )
    assert req.current_step == "submission"  # assignee="requester"

    assert await list_actionable(bob) == []
    actionable_alice = await list_actionable(alice)
    assert [r.id for r in actionable_alice] == [req.id]


async def test_list_actionable_matches_can_user_act_for_permission_steps():
    """When a step sets assignee_permission, a user lacking that permission
    must not match via the step's assignee_role fallback — can_user_act
    refuses them, so the actionable list must too."""
    from not_dot_net.backend.workflow_engine import can_user_act
    from not_dot_net.backend.workflow_service import workflows_config

    await _setup_roles()
    cfg = await roles_config.get()
    cfg.roles["director"] = RoleDefinition(label="Director", permissions=["create_workflows"])
    await roles_config.set(cfg)

    staff = await _create_user(email="staff@test.com", role="staff")
    fake_director = await _create_user(email="fake@test.com", role="director")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    # approval step: assignee_role="director" + assignee_permission="approve_workflows";
    # fake_director has the role but not the permission
    req = await get_request_by_id(req.id)
    wf = (await workflows_config.get()).workflows["vpn_access"]
    assert not await can_user_act(fake_director, req, wf)
    assert await list_actionable(fake_director) == []


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
    req = await submit_step(req.id, director.id, "approve", data={}, actor_user=director, ad_creds=("admin", "pass"))
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


async def test_save_draft_rejects_step_without_partial_save():
    """save_draft must refuse to write data on steps where partial_save is False."""
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    director = await _create_user(email="director@test.com", role="director")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )
    req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    assert req.current_step == "approval"
    with pytest.raises(PermissionError, match="partial_save"):
        await save_draft(req.id, data={"target_name": "Hacker"}, actor_user=director)


async def test_submit_step_rejects_unknown_action():
    """submit_step must refuse actions that are not in step.actions."""
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )
    with pytest.raises(ValueError, match="not allowed"):
        await submit_step(req.id, staff.id, "evil_action", data={}, actor_user=staff)


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
    req = await submit_step(req.id, director.id, "approve", data={}, actor_user=director, ad_creds=("admin", "pass"))
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


async def test_onboarding_v2_full_flow(monkeypatch):
    """Test the complete 4-step onboarding: initiation → newcomer_info → admin_validation → it_account_creation."""
    await _setup_roles()
    cfg = await roles_config.get()
    cfg.roles["admin"].permissions.append("access_personal_data")
    cfg.roles["admin"].permissions.append("manage_users")
    await roles_config.set(cfg)

    initiator = await _create_user(email="initiator@test.com", role="staff")
    admin = await _create_user(email="admin@test.com", role="admin")

    # Monkeypatch AD primitives to bypass LDAP
    import not_dot_net.backend.workflow_service as ws
    monkeypatch.setattr(ws, "ldap_lookup_by_sam", lambda *a, **kw: None)
    monkeypatch.setattr(ws, "ldap_create_user",
                        lambda new_user, bu, bp, cfg, connect=None: f"CN={new_user.display_name},OU=Users,DC=x,DC=y")
    monkeypatch.setattr(ws, "ldap_add_to_groups", lambda *a, **kw: {})

    # Create target user for ad_account_creation step
    newcomer = await _create_user(email="newcomer@example.com", role="staff")

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

    # Step 4: IT marks complete (ad_account_creation step requires AD creds and form data)
    from not_dot_net.backend.ad_account_config import ad_account_config
    ad_cfg = await ad_account_config.get()
    await ad_account_config.set(ad_cfg.model_copy(update={
        "users_ous": ["OU=Users,DC=x,DC=y"],
        "eligible_groups": [],
    }))
    
    req = await submit_step(
        req.id, admin.id, "complete",
        data={
            "first_name": "Marie", "last_name": "Curie",
            "sam_account": "mcurie", "ou_dn": "OU=Users,DC=x,DC=y",
            "mail": "marie.curie@example.com", "home_directory": "/home/mcurie",
            "groups": [],
        },
        actor_user=admin,
        ad_creds=("admin", "password"),
    )
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


async def test_onboarding_target_token_cannot_be_replayed_after_submit():
    await _setup_roles()
    initiator = await _create_user(email="initiator@test.com", role="staff")

    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "newcomer@example.com", "status": "PhD"},
        actor=initiator,
    )
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)
    target_token = req.token
    assert target_token is not None

    req = await submit_step(
        req.id,
        actor_id=None,
        action="submit",
        data={"first_name": "Marie", "last_name": "Curie"},
        actor_token=target_token,
    )
    assert req.current_step == "admin_validation"
    assert req.token is None

    with pytest.raises(PermissionError):
        await save_draft(req.id, data={"phone": "replay"}, actor_token=target_token)


async def test_onboarding_target_token_is_bound_to_its_request():
    await _setup_roles()
    initiator = await _create_user(email="initiator@test.com", role="staff")

    req_a = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "a@example.com", "status": "PhD"},
        actor=initiator,
    )
    req_a = await submit_step(req_a.id, initiator.id, "submit", data={}, actor_user=initiator)
    assert req_a.token is not None

    req_b = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "b@example.com", "status": "CDD"},
        actor=initiator,
    )
    req_b = await submit_step(req_b.id, initiator.id, "submit", data={}, actor_user=initiator)

    with pytest.raises(PermissionError):
        await save_draft(req_b.id, data={"phone": "wrong request"}, actor_token=req_a.token)


async def test_onboarding_target_step_blocks_unrelated_user_without_token():
    await _setup_roles()
    initiator = await _create_user(email="initiator@test.com", role="staff")
    intruder = await _create_user(email="intruder@example.com", role="member")

    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "newcomer@example.com", "status": "PhD"},
        actor=initiator,
    )
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)
    assert req.current_step == "newcomer_info"

    with pytest.raises(PermissionError):
        await submit_step(
            req.id,
            intruder.id,
            "submit",
            data={"first_name": "Mallory", "last_name": "Evil"},
            actor_user=intruder,
        )


async def test_onboarding_completion_marks_encrypted_files_for_retention(monkeypatch):
    await _setup_roles()
    cfg = await roles_config.get()
    cfg.roles["admin"].permissions.append("access_personal_data")
    cfg.roles["admin"].permissions.append("manage_users")
    await roles_config.set(cfg)

    initiator = await _create_user(email="initiator@test.com", role="staff")
    admin = await _create_user(email="admin@test.com", role="admin")

    # Monkeypatch AD primitives to bypass LDAP
    import not_dot_net.backend.workflow_service as ws
    monkeypatch.setattr(ws, "ldap_lookup_by_sam", lambda *a, **kw: None)
    monkeypatch.setattr(ws, "ldap_create_user",
                        lambda new_user, bu, bp, cfg, connect=None: f"CN={new_user.display_name},OU=Users,DC=x,DC=y")
    monkeypatch.setattr(ws, "ldap_add_to_groups", lambda *a, **kw: {})

    # Create target user for ad_account_creation step
    newcomer = await _create_user(email="newcomer@example.com", role="staff")

    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={"contact_email": "newcomer@example.com", "status": "PhD", "employer": "CNRS"},
        actor=initiator,
    )
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)

    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        enc_file = EncryptedFile(
            wrapped_dek=b"wrapped",
            nonce=b"nonce",
            storage_path="data/encrypted/test.enc",
            original_filename="id.pdf",
            content_type="application/pdf",
        )
        session.add(enc_file)
        await session.flush()
        session.add(WorkflowFile(
            request_id=req.id,
            step_key="newcomer_info",
            field_name="id_document",
            filename="id.pdf",
            storage_path="encrypted",
            encrypted_file_id=enc_file.id,
        ))
        await session.commit()
        encrypted_file_id = enc_file.id

    req = await submit_step(
        req.id,
        actor_id=None,
        action="submit",
        data={"first_name": "Marie", "last_name": "Curie"},
        actor_token=req.token,
    )
    req = await submit_step(req.id, admin.id, "approve", data={}, actor_user=admin)
    
    # Set up AD config and credentials for the complete action
    from not_dot_net.backend.ad_account_config import ad_account_config
    ad_cfg = await ad_account_config.get()
    await ad_account_config.set(ad_cfg.model_copy(update={
        "users_ous": ["OU=Users,DC=x,DC=y"],
        "eligible_groups": [],
    }))
    
    req = await submit_step(
        req.id, admin.id, "complete",
        data={
            "first_name": "Marie", "last_name": "Curie",
            "sam_account": "mcurie", "ou_dn": "OU=Users,DC=x,DC=y",
            "mail": "marie.curie@example.com", "home_directory": "/home/mcurie",
            "groups": [],
        },
        actor_user=admin,
        ad_creds=("admin", "password"),
    )
    assert req.status == "completed"

    async with get_session() as session:
        enc_file = await session.get(EncryptedFile, encrypted_file_id)
        assert enc_file.retained_until is not None


# ---------------------------------------------------------------------------
# B-T2: submit_step must refuse to act on a terminal (completed/rejected/
# cancelled) request. Otherwise a second call re-executes the action —
# duplicate events, re-fired notifications, possibly re-run AD effects.
# ---------------------------------------------------------------------------


async def test_submit_step_rejects_action_on_completed_request():
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    director = await _create_user(email="director@test.com", role="director")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )
    req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    req = await submit_step(req.id, director.id, "approve", data={}, actor_user=director, ad_creds=("admin", "pass"))
    assert req.status == "completed"

    with pytest.raises(ValueError, match="Cannot act"):
        await submit_step(req.id, director.id, "approve", data={}, actor_user=director, ad_creds=("admin", "pass"))


async def test_submit_step_rejects_action_on_rejected_request():
    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    director = await _create_user(email="director@test.com", role="director")
    req = await create_request(
        workflow_type="vpn_access",
        created_by=staff.id,
        data={"target_name": "Alice", "target_email": "alice@test.com"},
    )
    req = await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)
    req = await submit_step(req.id, director.id, "reject", data={}, comment="no", actor_user=director)
    assert req.status == "rejected"

    # A rejected request must not be resurrectable into "completed".
    with pytest.raises(ValueError, match="Cannot act"):
        await submit_step(req.id, director.id, "approve", data={}, actor_user=director, ad_creds=("admin", "pass"))


# ---------------------------------------------------------------------------
# B-T7: list_events_batch groups events by request, ordered by created_at.
# ---------------------------------------------------------------------------


async def test_list_events_batch_groups_by_request():
    from not_dot_net.backend.workflow_service import list_events_batch

    await _setup_roles()
    staff = await _create_user(email="staff@test.com", role="staff")
    r1 = await create_request(
        workflow_type="vpn_access", created_by=staff.id,
        data={"target_name": "A", "target_email": "a@test.com"},
    )
    r2 = await create_request(
        workflow_type="vpn_access", created_by=staff.id,
        data={"target_name": "B", "target_email": "b@test.com"},
    )
    await submit_step(r1.id, staff.id, "submit", data={}, actor_user=staff)

    batch = await list_events_batch([r1.id, r2.id])

    assert set(batch.keys()) == {r1.id, r2.id}
    assert [e.action for e in batch[r1.id]] == ["create", "submit"]
    assert [e.action for e in batch[r2.id]] == ["create"]


async def test_list_events_batch_empty():
    from not_dot_net.backend.workflow_service import list_events_batch

    assert await list_events_batch([]) == {}
