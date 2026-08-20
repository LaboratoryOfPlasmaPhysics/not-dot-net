import pytest
import uuid
from contextlib import asynccontextmanager
from datetime import date

from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.workflow_service import create_request, submit_step
from not_dot_net.backend.workflow_config import workflows_config
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.tenure_service import list_tenures


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
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=["create_workflows"])
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=[
            "create_workflows",
            "approve_workflows",
            "access_personal_data",
            "manage_users",
        ],
    )
    await roles_config.set(cfg)


async def test_onboarding_initiation_has_employer_field():
    cfg = await workflows_config.get()
    onboarding = cfg.workflows["onboarding"]
    initiation = onboarding.steps[0]
    field_names = [f.name for f in initiation.fields]
    assert "employer" in field_names
    employer_field = next(f for f in initiation.fields if f.name == "employer")
    assert employer_field.type == "select"
    assert employer_field.options_key == "employers"


async def test_onboarding_employer_stored_in_request_data():
    await _setup_roles()
    user = await _create_user()
    req = await create_request(
        workflow_type="onboarding",
        created_by=user.id,
        data={"contact_email": "new@test.com", "status": "PhD", "employer": "CNRS"},
        actor=user,
    )
    assert req.data["employer"] == "CNRS"


async def test_employers_vocabulary_has_cnrs():
    from not_dot_net.backend.vocabularies import (
        vocabularies_config,
        VocabulariesConfig,
        StoredVocabulary,
        VocabularyTerm,
        resolve_terms,
    )
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "employers": StoredVocabulary(
            key="employers",
            label={"en": "Employers"},
            terms=[VocabularyTerm(code="CNRS", labels={"en": "CNRS"})],
        ),
    }))
    employers = [term.code for term in await resolve_terms("employers")]
    assert "CNRS" in employers


async def test_create_tenure_from_onboarding_direct():
    """Test _create_tenure_from_onboarding helper directly."""
    from not_dot_net.backend.workflow_service import _create_tenure_from_onboarding
    from not_dot_net.backend.workflow_models import WorkflowRequest, RequestStatus

    user = await _create_user("newcomer@test.com")

    # Create a mock completed request with onboarding data
    from not_dot_net.backend.db import session_scope
    async with session_scope() as session:
        req = WorkflowRequest(
            type="onboarding",
            current_step="it_account_creation",
            status=RequestStatus.COMPLETED,
            data={"status": "PhD", "employer": "CNRS", "contact_email": "newcomer@test.com"},
            created_by=user.id,
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)

    await _create_tenure_from_onboarding(req, user.id)
    tenures = await list_tenures(user.id)
    assert len(tenures) == 1
    assert tenures[0].status == "PhD"
    assert tenures[0].employer == "CNRS"
    assert tenures[0].start_date == date.today()


async def test_create_tenure_from_onboarding_with_start_date():
    """Test that start_date from request data is used if present."""
    from not_dot_net.backend.workflow_service import _create_tenure_from_onboarding
    from not_dot_net.backend.workflow_models import WorkflowRequest, RequestStatus

    user = await _create_user("newcomer2@test.com")

    from not_dot_net.backend.db import session_scope
    async with session_scope() as session:
        req = WorkflowRequest(
            type="onboarding",
            current_step="it_account_creation",
            status=RequestStatus.COMPLETED,
            data={"status": "Intern", "employer": "Polytechnique", "start_date": "2026-06-01"},
            created_by=user.id,
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)

    await _create_tenure_from_onboarding(req, user.id)
    tenures = await list_tenures(user.id)
    assert len(tenures) == 1
    assert tenures[0].start_date == date(2026, 6, 1)


async def test_create_tenure_skipped_without_employer():
    """No tenure created if employer is missing from request data."""
    from not_dot_net.backend.workflow_service import _create_tenure_from_onboarding
    from not_dot_net.backend.workflow_models import WorkflowRequest, RequestStatus

    user = await _create_user("newcomer3@test.com")

    from not_dot_net.backend.db import session_scope
    async with session_scope() as session:
        req = WorkflowRequest(
            type="onboarding",
            current_step="it_account_creation",
            status=RequestStatus.COMPLETED,
            data={"status": "PhD"},
            created_by=user.id,
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)

    await _create_tenure_from_onboarding(req, user.id)
    tenures = await list_tenures(user.id)
    assert len(tenures) == 0


async def test_create_tenure_skipped_without_status():
    """No tenure created if status is missing from request data."""
    from not_dot_net.backend.workflow_service import _create_tenure_from_onboarding
    from not_dot_net.backend.workflow_models import WorkflowRequest, RequestStatus

    user = await _create_user("newcomer4@test.com")

    from not_dot_net.backend.db import session_scope
    async with session_scope() as session:
        req = WorkflowRequest(
            type="onboarding",
            current_step="it_account_creation",
            status=RequestStatus.COMPLETED,
            data={"employer": "CNRS"},
            created_by=user.id,
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)

    await _create_tenure_from_onboarding(req, user.id)
    tenures = await list_tenures(user.id)
    assert len(tenures) == 0


async def test_create_tenure_from_onboarding_invalid_start_date_falls_back_to_today():
    """Invalid start_date is ignored instead of crashing tenure creation."""
    from not_dot_net.backend.workflow_service import _create_tenure_from_onboarding
    from not_dot_net.backend.workflow_models import WorkflowRequest, RequestStatus

    user = await _create_user("newcomer5@test.com")

    from not_dot_net.backend.db import session_scope
    async with session_scope() as session:
        req = WorkflowRequest(
            type="onboarding",
            current_step="it_account_creation",
            status=RequestStatus.COMPLETED,
            data={"status": "PhD", "employer": "CNRS", "start_date": "not-a-date"},
            created_by=user.id,
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)

    await _create_tenure_from_onboarding(req, user.id)
    tenures = await list_tenures(user.id)
    assert len(tenures) == 1
    assert tenures[0].start_date == date.today()


async def test_onboarding_completion_creates_tenure_for_target_email_user(monkeypatch):
    await _setup_roles()
    initiator = await _create_user("initiator@test.com", role="staff")
    admin = await _create_user("admin@test.com", role="admin")
    target = await _create_user("target-newcomer@test.com", role="staff")

    # Monkeypatch AD primitives to bypass LDAP
    import not_dot_net.backend.workflow_ad_account as wa
    import not_dot_net.backend.workflow_service as ws
    monkeypatch.setattr(wa, "ldap_lookup_by_sam", lambda *a, **kw: None)
    monkeypatch.setattr(wa, "ldap_create_user",
                        lambda new_user, bu, bp, cfg, connect=None: f"CN={new_user.display_name},OU=Users,DC=x,DC=y")
    monkeypatch.setattr(wa, "ldap_add_to_groups", lambda *a, **kw: {})

    # Set up AD config
    from not_dot_net.backend.ad_account_config import ad_account_config
    ad_cfg = await ad_account_config.get()
    await ad_account_config.set(ad_cfg.model_copy(update={
        "users_ous": ["OU=Users,DC=x,DC=y"],
        "eligible_groups": [],
    }))

    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={
            "contact_email": target.email,
            "status": "PhD",
            "employer": "CNRS",
            "start_date": "2026-09-01",
        },
        actor=initiator,
    )
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)
    req = await submit_step(
        req.id,
        actor_id=None,
        action="submit",
        data={"first_name": "Marie", "last_name": "Curie"},
        actor_token=req.token,
    )
    req = await submit_step(req.id, admin.id, "approve", data={}, actor_user=admin)
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
    tenures = await list_tenures(target.id)
    assert len(tenures) == 1
    assert tenures[0].status == "PhD"
    assert tenures[0].employer == "CNRS"
    assert tenures[0].start_date == date(2026, 9, 1)


async def test_onboarding_completion_uses_returning_user_id_for_tenure_target(monkeypatch):
    await _setup_roles()
    initiator = await _create_user("initiator-returning@test.com", role="staff")
    admin = await _create_user("admin-returning@test.com", role="admin")
    returning_user = await _create_user("returning@test.com", role="staff")

    # Monkeypatch AD primitives to bypass LDAP
    import not_dot_net.backend.workflow_ad_account as wa
    import not_dot_net.backend.workflow_service as ws
    monkeypatch.setattr(wa, "ldap_lookup_by_sam", lambda *a, **kw: None)
    monkeypatch.setattr(wa, "ldap_create_user",
                        lambda new_user, bu, bp, cfg, connect=None: f"CN={new_user.display_name},OU=Users,DC=x,DC=y")
    monkeypatch.setattr(wa, "ldap_add_to_groups", lambda *a, **kw: {})

    # Set up AD config
    from not_dot_net.backend.ad_account_config import ad_account_config
    ad_cfg = await ad_account_config.get()
    await ad_account_config.set(ad_cfg.model_copy(update={
        "users_ous": ["OU=Users,DC=x,DC=y"],
        "eligible_groups": [],
    }))

    req = await create_request(
        workflow_type="onboarding",
        created_by=initiator.id,
        data={
            "contact_email": returning_user.email,  # Use the returning user's email
            "returning_user_id": str(returning_user.id),
            "status": "CDD",
            "employer": "Polytechnique",
            "start_date": "2026-01-15",
        },
        actor=initiator,
    )
    req = await submit_step(req.id, initiator.id, "submit", data={}, actor_user=initiator)
    req = await submit_step(
        req.id,
        actor_id=None,
        action="submit",
        data={"first_name": "Ada", "last_name": "Lovelace"},
        actor_token=req.token,
    )
    req = await submit_step(req.id, admin.id, "approve", data={}, actor_user=admin)
    req = await submit_step(
        req.id, admin.id, "complete",
        data={
            "first_name": "Ada", "last_name": "Lovelace",
            "sam_account": "alovelace", "ou_dn": "OU=Users,DC=x,DC=y",
            "mail": "ada.lovelace@example.com", "home_directory": "/home/alovelace",
            "groups": [],
        },
        actor_user=admin,
        ad_creds=("admin", "password"),
    )

    assert req.status == "completed"
    tenures = await list_tenures(returning_user.id)
    assert len(tenures) == 1
    assert tenures[0].status == "CDD"
    assert tenures[0].employer == "Polytechnique"


async def test_tenure_options_from_registry():
    from not_dot_net.backend.vocabularies import (
        vocabularies_config, VocabulariesConfig, StoredVocabulary, VocabularyTerm)
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "employment_statuses": StoredVocabulary(
            key="employment_statuses", label={"en": "S"},
            terms=[VocabularyTerm(code="PostDoc", labels={"en": "PostDoc"})]),
        "employers": StoredVocabulary(
            key="employers", label={"en": "E"},
            terms=[VocabularyTerm(code="CNRS", labels={"en": "CNRS"})]),
    }))
    from not_dot_net.frontend.directory import _tenure_options
    status, employer = await _tenure_options("en")
    assert status == {"PostDoc": "PostDoc"}
    assert employer == {"CNRS": "CNRS"}
