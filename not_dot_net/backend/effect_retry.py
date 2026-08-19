"""Durable queue of workflow step effects that failed.

`submit_step` commits the step transition before running effects, so a failing
AD write used to leave the request COMPLETED while AD stayed untouched — with
only a log line to show for it. Failures now land here instead.

Unlike `mail_outbox` there is no background worker: effects bind to AD as the
acting admin (this deployment has no service account), so nothing outside a
logged-in admin's session holds credentials to retry with. Rows therefore
survive until someone drains them from the request page, which re-prompts for
credentials. Give the app a delegated AD service account and this becomes a
timer-driven worker instead.
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, String, Text, func, select
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base, session_scope

logger = logging.getLogger("not_dot_net.effect_retry")


class FailedEffect(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "workflow_failed_effect"

    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_request.id", ondelete="CASCADE"), index=True
    )
    step_key: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))
    kind: Mapped[str] = mapped_column(String(100))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    params: Mapped[dict] = mapped_column(JSON, default_factory=dict)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), default=None, index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)


def describe_failure(result) -> str:
    """One-line reason from an EffectResult, for display and for last_error."""
    if result.failures:
        return "; ".join(f"{k}: {v}" for k, v in result.failures.items())[:1024]
    reason = result.detail.get("reason") if result.detail else None
    return (reason or "effect reported failure")[:1024]


async def enqueue_failed_effect(
    *, request_id: uuid.UUID, step_key: str, action: str,
    kind: str, params: dict, error: str,
) -> None:
    """Persist one failed effect so an admin can retry it later."""
    async with session_scope() as session:
        session.add(FailedEffect(
            request_id=request_id, step_key=step_key, action=action,
            kind=kind, params=dict(params or {}), last_error=error[:1024],
        ))
        await session.commit()


async def pending_effects(request_id: uuid.UUID | None = None) -> list[FailedEffect]:
    """Unresolved failed effects, newest last. All requests when id is None."""
    async with session_scope() as session:
        query = select(FailedEffect).where(FailedEffect.resolved_at.is_(None))
        if request_id is not None:
            query = query.where(FailedEffect.request_id == request_id)
        result = await session.execute(query.order_by(FailedEffect.created_at))
        return list(result.scalars().all())


async def pending_effect_count(request_id: uuid.UUID | None = None) -> int:
    return len(await pending_effects(request_id))


async def retry_pending_effects(
    request_id: uuid.UUID, *, ad_creds: tuple[str, str], actor,
) -> tuple[int, int]:
    """Re-run every unresolved effect on a request. Returns (resolved, still_failing).

    Each row commits on its own so one stubborn effect can't roll back the
    others' resolution.
    """
    from not_dot_net.backend.workflow_effects import EFFECT_REGISTRY
    from not_dot_net.backend.workflow_models import WorkflowRequest

    rows = await pending_effects(request_id)
    resolved = 0
    still_failing = 0

    for row_id in [r.id for r in rows]:
        async with session_scope() as session:
            row = await session.get(FailedEffect, row_id)
            if row is None or row.resolved_at is not None:
                continue
            request = await session.get(WorkflowRequest, row.request_id)
            handler = EFFECT_REGISTRY.get(row.kind)

            error = None
            if request is None:
                error = "request no longer exists"
            elif handler is None:
                error = f"unknown effect kind: {row.kind}"
            else:
                try:
                    result = await handler.run(
                        request, None, row.action, row.params, ad_creds, actor,
                    )
                    error = None if result.succeeded else describe_failure(result)
                except Exception as exc:
                    error = str(exc)[:1024]

            if error is None:
                row.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
                resolved += 1
            else:
                row.attempts += 1
                row.last_error = error
                still_failing += 1
            await session.commit()

    await _audit_retry(request_id, actor, resolved, still_failing)
    return resolved, still_failing


async def _audit_retry(request_id, actor, resolved: int, still_failing: int) -> None:
    from not_dot_net.backend.audit import log_audit

    await log_audit(
        category="ad", action="retry_effects",
        actor_id=str(getattr(actor, "id", None)) if actor else None,
        target_id=str(request_id),
        detail=f"resolved={resolved} still_failing={still_failing}",
    )
