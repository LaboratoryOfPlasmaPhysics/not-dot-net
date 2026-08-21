import random
import uuid
from types import SimpleNamespace

import pytest

from not_dot_net.backend.seeding import (
    _choose_workflow_approver,
    _seed_fake_workflows,
    _workflow_creator_candidates,
)


def _user(role: str):
    user_id = uuid.uuid4()
    return SimpleNamespace(id=user_id, role=role, email=f"{role}-{user_id}@test.com")


def test_workflow_creator_candidates_prefer_regular_staff():
    staff = _user("staff")
    director = _user("director")

    assert _workflow_creator_candidates([director, staff]) == [staff]


def test_workflow_creator_candidates_fall_back_to_directors():
    director = _user("director")

    assert _workflow_creator_candidates([director]) == [director]


def test_choose_workflow_approver_avoids_creator_when_possible():
    rng = random.Random(42)
    creator = _user("director")
    other_director = _user("director")

    assert _choose_workflow_approver(rng, [creator, other_director], creator) == other_director


def test_choose_workflow_approver_falls_back_to_creator_without_directors():
    rng = random.Random(42)
    creator = _user("staff")

    assert _choose_workflow_approver(rng, [], creator) == creator


def test_onboarding_workflow_seeds_use_current_schema():
    from not_dot_net.backend.seed_data import WORKFLOW_SEEDS

    legacy_fields = {"person_name", "person_email", "role_status", "team"}
    target_person_steps = {"newcomer_info", "admin_validation", "done", "rejected"}
    for seed in WORKFLOW_SEEDS:
        if seed["type"] != "onboarding":
            continue
        data = seed["data"]
        assert "contact_email" in data
        assert "status" in data
        assert "employer" in data
        assert not legacy_fields.intersection(data)
        if seed["step"] in target_person_steps:
            assert "first_name" in data
            assert "last_name" in data


@pytest.mark.asyncio
async def test_seed_fake_workflows_submits_with_valid_actors(monkeypatch):
    from not_dot_net.backend import seed_data, seeding, workflow_service

    creator = _user("staff")
    approver = _user("director")
    request_id = uuid.uuid4()
    calls = []
    backdated = []

    monkeypatch.setattr(
        seed_data,
        "WORKFLOW_SEEDS",
        [
            {
                "type": "onboarding",
                "step": "done",
                "action": "approve",
                "data": {"contact_email": "newcomer@test.com"},
            },
        ],
    )

    async def fake_create_request(*, workflow_type, created_by, data, actor=None):
        assert actor is not None, "seeding must pass an explicit actor"
        return SimpleNamespace(id=request_id, token=None)

    async def fake_submit_step(request_id, actor_id, action, **kwargs):
        actor_user = kwargs.get("actor_user")
        calls.append(
            {
                "actor_id": actor_id,
                "action": action,
                "actor_user_id": getattr(actor_user, "id", None),
                "actor_user_is_superuser": getattr(actor_user, "is_superuser", None),
                "actor_token": kwargs.get("actor_token"),
                "ad_creds": kwargs.get("ad_creds"),
            }
        )
        if actor_id == creator.id:
            return SimpleNamespace(id=request_id, token="target-token")
        return SimpleNamespace(id=request_id, token=None)

    monkeypatch.setattr(workflow_service, "create_request", fake_create_request)
    monkeypatch.setattr(workflow_service, "submit_step", fake_submit_step)

    async def fake_backdate_workflow_request(request_id, days):
        backdated.append({"request_id": request_id, "days": days})

    monkeypatch.setattr(seeding, "_backdate_workflow_request", fake_backdate_workflow_request)

    await _seed_fake_workflows([creator, approver])

    assert calls == [
        {
            "actor_id": creator.id,
            "action": "submit",
            "actor_user_id": creator.id,
            "actor_user_is_superuser": True,
            "actor_token": None,
            "ad_creds": None,
        },
        {
            "actor_id": None,
            "action": "submit",
            "actor_user_id": None,
            "actor_user_is_superuser": None,
            "actor_token": "target-token",
            "ad_creds": None,
        },
        {
            "actor_id": approver.id,
            "action": "approve",
            "actor_user_id": approver.id,
            "actor_user_is_superuser": True,
            "actor_token": None,
            "ad_creds": ("seed", "seed"),
        },
    ]
    assert len(backdated) == 1
    assert backdated[0]["request_id"] == request_id
    assert 0 <= backdated[0]["days"] <= 10
