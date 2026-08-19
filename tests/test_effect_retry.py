"""I1 — a step effect that fails after the commit must not vanish.

`submit_step` commits the transition and only then runs AD effects, swallowing
failures into the log. An offboarding workflow whose final step declares
`ad_disable_account` could end up COMPLETED with the AD account still enabled
and nobody told. Failed effects are now persisted so they can be retried.

Effects bind as the acting admin (there is no AD service account), so the queue
is drained by a human with the credential prompt, not by a background worker.
"""
import uuid

import pytest

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.workflow_models import WorkflowRequest


async def _make_request(status: str = "completed") -> uuid.UUID:
    async with session_scope() as session:
        req = WorkflowRequest(
            type="offboarding", status=status, current_step="done",
            target_email="leaver@example.com",
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
        return req.id


class _FakeStep:
    def __init__(self, effects):
        self.key = "final"
        self.effects = effects


class _FakeEffect:
    def __init__(self, kind, on_action="approve", params=None):
        self.kind = kind
        self.on_action = on_action
        self.params = params or {}


async def test_failed_effect_is_persisted(monkeypatch):
    """A handler returning succeeded=False leaves a pending row behind."""
    from not_dot_net.backend import workflow_effects
    from not_dot_net.backend.effect_retry import pending_effects
    from not_dot_net.backend.workflow_effects import EffectResult, run_effects

    request_id = await _make_request()

    async def failing_run(request, step, action, params, ad_creds, actor):
        return EffectResult(
            kind="ad_disable_account", succeeded=False,
            detail={"target_dn": "cn=leaver,dc=example,dc=com"},
            failures={"_modify": "insufficient access rights"},
        )

    monkeypatch.setattr(
        workflow_effects.EFFECT_REGISTRY["ad_disable_account"], "run", failing_run
    )

    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)

    step = _FakeStep([_FakeEffect("ad_disable_account")])
    results = await run_effects(
        request=req, step=step, action="approve", ad_creds=("admin", "pw"), actor=None,
    )
    assert results[0].succeeded is False

    pending = await pending_effects(request_id)
    assert len(pending) == 1
    assert pending[0].kind == "ad_disable_account"
    assert pending[0].step_key == "final"
    assert pending[0].action == "approve"
    assert "insufficient access rights" in pending[0].last_error


async def test_successful_effect_leaves_no_row(monkeypatch):
    from not_dot_net.backend import workflow_effects
    from not_dot_net.backend.effect_retry import pending_effects
    from not_dot_net.backend.workflow_effects import EffectResult, run_effects

    request_id = await _make_request()

    async def ok_run(request, step, action, params, ad_creds, actor):
        return EffectResult(kind="ad_disable_account", succeeded=True, detail={})

    monkeypatch.setattr(
        workflow_effects.EFFECT_REGISTRY["ad_disable_account"], "run", ok_run
    )
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)

    await run_effects(
        request=req, step=_FakeStep([_FakeEffect("ad_disable_account")]),
        action="approve", ad_creds=("admin", "pw"), actor=None,
    )
    assert await pending_effects(request_id) == []


async def test_retry_resolves_the_row_on_success(monkeypatch):
    """Retrying a queued effect with working credentials clears it."""
    from not_dot_net.backend import workflow_effects
    from not_dot_net.backend.effect_retry import pending_effects, retry_pending_effects
    from not_dot_net.backend.workflow_effects import EffectResult, run_effects

    request_id = await _make_request()
    outcome = {"succeed": False}

    async def flaky_run(request, step, action, params, ad_creds, actor):
        return EffectResult(
            kind="ad_disable_account", succeeded=outcome["succeed"],
            failures={} if outcome["succeed"] else {"_modify": "server down"},
        )

    monkeypatch.setattr(
        workflow_effects.EFFECT_REGISTRY["ad_disable_account"], "run", flaky_run
    )
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)

    await run_effects(
        request=req, step=_FakeStep([_FakeEffect("ad_disable_account")]),
        action="approve", ad_creds=("admin", "pw"), actor=None,
    )
    assert len(await pending_effects(request_id)) == 1

    outcome["succeed"] = True
    ok, failed = await retry_pending_effects(request_id, ad_creds=("admin", "pw"), actor=None)
    assert (ok, failed) == (1, 0)
    assert await pending_effects(request_id) == []


async def test_retry_failure_increments_attempts_and_keeps_row(monkeypatch):
    from not_dot_net.backend import workflow_effects
    from not_dot_net.backend.effect_retry import pending_effects, retry_pending_effects
    from not_dot_net.backend.workflow_effects import EffectResult, run_effects

    request_id = await _make_request()

    async def always_fails(request, step, action, params, ad_creds, actor):
        return EffectResult(kind="ad_disable_account", succeeded=False,
                            failures={"_modify": "still down"})

    monkeypatch.setattr(
        workflow_effects.EFFECT_REGISTRY["ad_disable_account"], "run", always_fails
    )
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)

    await run_effects(
        request=req, step=_FakeStep([_FakeEffect("ad_disable_account")]),
        action="approve", ad_creds=("admin", "pw"), actor=None,
    )
    ok, failed = await retry_pending_effects(request_id, ad_creds=("admin", "pw"), actor=None)
    assert (ok, failed) == (0, 1)

    pending = await pending_effects(request_id)
    assert len(pending) == 1
    assert pending[0].attempts == 1


async def test_handler_raising_is_also_queued(monkeypatch):
    """An unexpected exception inside a handler must not lose the effect either."""
    from not_dot_net.backend import workflow_effects
    from not_dot_net.backend.effect_retry import pending_effects
    from not_dot_net.backend.workflow_effects import run_effects

    request_id = await _make_request()

    async def boom(request, step, action, params, ad_creds, actor):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(
        workflow_effects.EFFECT_REGISTRY["ad_disable_account"], "run", boom
    )
    async with session_scope() as session:
        req = await session.get(WorkflowRequest, request_id)

    results = await run_effects(
        request=req, step=_FakeStep([_FakeEffect("ad_disable_account")]),
        action="approve", ad_creds=("admin", "pw"), actor=None,
    )
    assert results[0].succeeded is False

    pending = await pending_effects(request_id)
    assert len(pending) == 1
    assert "connection reset" in pending[0].last_error
