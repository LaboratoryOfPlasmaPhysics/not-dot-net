"""Generic 'workflow step → AD effect' framework.

Each WorkflowStepConfig may declare `effects: list[StepEffectConfig]`.
At step transition time, matching effects fire in declared order.
Failures are collected and audit-logged; they do not abort the chain.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, ClassVar

from not_dot_net.config import StepEffectConfig  # noqa: F401 — re-exported for callers
from not_dot_net.backend.auth.ldap import (
    ldap_config,
    ldap_add_to_groups as _ldap_add_to_groups,
    ldap_remove_from_groups as _ldap_remove_from_groups,
    ldap_set_account_enabled as _ldap_set_account_enabled,
    LdapModifyError,
)
from not_dot_net.backend.ad_account_config import ad_account_config

logger = logging.getLogger("not_dot_net.workflow_effects")


class AdCredentialsRequired(Exception):
    """Raised by submit_step if effects need AD admin credentials and none were provided."""


def ensure_effect_credentials(step, action: str, ad_creds: tuple[str, str] | None) -> None:
    """Raise AdCredentialsRequired if effects matching `action` need AD creds.

    submit_step calls this BEFORE committing the step transition: the frontend
    catches the exception, prompts for credentials, and retries the same call —
    which only works if the no-creds attempt changed nothing.
    """
    matching = [e for e in (getattr(step, "effects", None) or []) if e.on_action == action]
    needs_creds = any(
        EFFECT_REGISTRY.get(e.kind) and EFFECT_REGISTRY[e.kind].requires_ad_credentials
        for e in matching
    )
    if needs_creds and not ad_creds:
        raise AdCredentialsRequired(
            f"Step '{getattr(step, 'key', '?')}' action '{action}' requires AD admin credentials"
        )


@dataclass(frozen=True)
class EffectResult:
    kind: str
    succeeded: bool
    detail: dict[str, Any] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)


async def _resolve_target_dn(request, target_key: str) -> str | None:
    """Resolve target spec to an LDAP DN. v1 supports 'target_person' only."""
    from not_dot_net.backend.db import session_scope, User
    from sqlalchemy import select, func

    if target_key != "target_person":
        return None
    if not request.target_email:
        return None
    async with session_scope() as session:
        u = (await session.execute(
            select(User).where(func.lower(User.email) == request.target_email.lower())
        )).scalar_one_or_none()
    return u.ldap_dn if u else None


class BaseEffectHandler:
    kind: ClassVar[str] = ""
    requires_ad_credentials: ClassVar[bool] = True

    def validate_params(self, params: dict) -> None:
        return None

    async def run(self, request, step, action, params, ad_creds, actor) -> EffectResult:
        raise NotImplementedError


class _GroupOpHandler(BaseEffectHandler):
    """Common base for add/remove from groups."""

    async def _eligible_groups(self) -> list[str]:
        cfg = await ad_account_config.get()
        return list(cfg.eligible_groups)

    def validate_params(self, params: dict) -> None:
        groups = params.get("groups") or []
        if not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
            raise ValueError("params.groups must be a list of DNs")

    def _call_op(self, target_dn: str, groups: list[str], bind_user: str, bind_pw: str, cfg) -> dict[str, str]:
        raise NotImplementedError

    async def run(self, request, step, action, params, ad_creds, actor) -> EffectResult:
        eligible = set(await self._eligible_groups())
        groups = params.get("groups") or []
        bad = [g for g in groups if g not in eligible]
        if bad:
            raise ValueError(f"groups not in eligible_groups: {bad}")

        target_key = params.get("target", "target_person")
        target_dn = await _resolve_target_dn(request, target_key)
        if not target_dn:
            return EffectResult(kind=self.kind, succeeded=False,
                                detail={"reason": "target has no ldap_dn", "target_key": target_key})

        bind_user, bind_pw = ad_creds
        cfg = await ldap_config.get()
        try:
            import asyncio
            failures = await asyncio.to_thread(
                self._call_op, target_dn, groups, bind_user, bind_pw, cfg,
            )
        except LdapModifyError as e:
            return EffectResult(kind=self.kind, succeeded=False,
                                detail={"target_dn": target_dn, "groups": groups},
                                failures={"_bind": str(e)})
        return EffectResult(
            kind=self.kind,
            succeeded=not failures,
            detail={"target_dn": target_dn, "groups": groups},
            failures=failures,
        )


class AdAddToGroupsHandler(_GroupOpHandler):
    kind = "ad_add_to_groups"

    def _call_op(self, target_dn, groups, bind_user, bind_pw, cfg):
        return _ldap_add_to_groups(target_dn, groups, bind_user, bind_pw, cfg)


class AdRemoveFromGroupsHandler(_GroupOpHandler):
    kind = "ad_remove_from_groups"

    def _call_op(self, target_dn, groups, bind_user, bind_pw, cfg):
        return _ldap_remove_from_groups(target_dn, groups, bind_user, bind_pw, cfg)


class _EnableHandler(BaseEffectHandler):
    kind = "ad_enable_account"
    enable: ClassVar[bool] = True

    async def run(self, request, step, action, params, ad_creds, actor) -> EffectResult:
        target_dn = await _resolve_target_dn(request, params.get("target", "target_person"))
        if not target_dn:
            return EffectResult(kind=self.kind, succeeded=False,
                                detail={"reason": "target has no ldap_dn"})
        cfg = await ldap_config.get()
        bind_user, bind_pw = ad_creds
        try:
            import asyncio
            await asyncio.to_thread(
                _ldap_set_account_enabled, target_dn, self.enable, bind_user, bind_pw, cfg,
            )
        except LdapModifyError as e:
            return EffectResult(kind=self.kind, succeeded=False,
                                detail={"target_dn": target_dn},
                                failures={"_modify": str(e)})
        return EffectResult(kind=self.kind, succeeded=True, detail={"target_dn": target_dn})


class AdEnableAccountHandler(_EnableHandler):
    kind = "ad_enable_account"
    enable = True


class AdDisableAccountHandler(_EnableHandler):
    kind = "ad_disable_account"
    enable = False


EFFECT_REGISTRY: dict[str, BaseEffectHandler] = {
    h.kind: h
    for h in [
        AdAddToGroupsHandler(),
        AdRemoveFromGroupsHandler(),
        AdEnableAccountHandler(),
        AdDisableAccountHandler(),
    ]
}


async def run_effects(
    *,
    request,
    step,
    action: str,
    ad_creds: tuple[str, str] | None,
    actor,
) -> list[EffectResult]:
    """Fire all effects on this step whose on_action matches.

    Raises AdCredentialsRequired if any matching effect needs creds and none were given.
    Audit-logs each effect's outcome.
    """
    from not_dot_net.backend.audit import log_audit

    matching = [e for e in (getattr(step, "effects", None) or []) if e.on_action == action]
    if not matching:
        return []
    ensure_effect_credentials(step, action, ad_creds)

    results: list[EffectResult] = []
    for effect in matching:
        handler = EFFECT_REGISTRY.get(effect.kind)
        if not handler:
            res = EffectResult(
                kind=effect.kind, succeeded=False,
                failures={"_kind": f"unknown effect kind: {effect.kind}"},
            )
            results.append(res)
            await log_audit(
                category="ad", action=effect.kind,
                actor_id=str(getattr(actor, "id", None)) if actor else None,
                target_id=None,
                detail=f"unknown_kind={effect.kind}",
            )
            continue
        try:
            res = await handler.run(request, step, action, effect.params, ad_creds, actor)
        except ValueError as e:
            res = EffectResult(kind=effect.kind, succeeded=False, failures={"_validation": str(e)})
        except Exception as e:
            logger.exception("Effect %s raised for request %s", effect.kind, request.id)
            res = EffectResult(kind=effect.kind, succeeded=False, failures={"_error": str(e)})
        results.append(res)
        failures_summary = ",".join(res.failures) if res.failures else ""
        await log_audit(
            category="ad", action=effect.kind,
            actor_id=str(getattr(actor, "id", None)) if actor else None,
            target_id=None,
            detail=f"succeeded={res.succeeded} failures={failures_summary}",
        )
        if not res.succeeded:
            await _queue_for_retry(request, step, action, effect, res)
    return results


async def _queue_for_retry(request, step, action: str, effect, result) -> None:
    """Persist a failed effect. The step transition is already committed, so
    losing this row means AD silently diverges from the workflow's own record."""
    from not_dot_net.backend.effect_retry import describe_failure, enqueue_failed_effect

    try:
        await enqueue_failed_effect(
            request_id=request.id,
            step_key=getattr(step, "key", "") or "",
            action=action,
            kind=effect.kind,
            params=effect.params,
            error=describe_failure(result),
        )
    except Exception:
        logger.exception("Could not queue failed effect %s for retry", effect.kind)
