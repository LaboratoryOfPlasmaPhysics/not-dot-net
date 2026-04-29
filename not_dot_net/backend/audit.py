"""Audit logging — structured event recording to DB + Python logging."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, String, Text, func, select
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base, session_scope

logger = logging.getLogger("not_dot_net.audit")


@dataclass
class AuditEventView:
    """Read-side view of an audit event with resolved display names.

    Decoupled from the ORM row so consumers can render without risk of
    dirty-writing the original actor_email or target_id columns back to disk.
    """
    id: uuid.UUID
    category: str
    action: str
    actor_id: str | None
    actor_email: str | None  # raw column value, untouched
    actor_display: str | None  # resolved name (or actor_email fallback)
    target_type: str | None
    target_id: str | None
    target_display: str | None
    detail: str | None
    metadata_json: dict | None
    created_at: datetime | None


class AuditEvent(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "audit_event"

    category: Mapped[str] = mapped_column(String(50), index=True)
    action: Mapped[str] = mapped_column(String(100))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, default=None)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None, index=True)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)  # user, request, resource, booking
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True, default=None)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None, index=True)


async def log_audit(
    category: str,
    action: str,
    actor_id: uuid.UUID | str | None = None,
    actor_email: str | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | str | None = None,
    detail: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Record an audit event to the database and Python logger."""
    actor_str = str(actor_id) if actor_id else None
    target_str = str(target_id) if target_id else None

    log_msg = f"[{category}] {action}"
    if actor_email:
        log_msg += f" by={actor_email}"
    if target_type and target_str:
        log_msg += f" {target_type}={target_str}"
    if detail:
        log_msg += f" — {detail}"
    logger.info(log_msg)

    try:
        async with session_scope() as session:
            event = AuditEvent(
                category=category,
                action=action,
                actor_id=actor_str,
                actor_email=actor_email,
                target_type=target_type,
                target_id=target_str,
                detail=detail,
                metadata_json=metadata,
            )
            session.add(event)
            await session.commit()
    except Exception:
        logger.exception("Failed to write audit event to DB")


async def list_audit_events(
    category: str | None = None,
    action: str | None = None,
    actor_email: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditEventView]:
    """Return audit events with resolved display names. The persisted rows
    are never mutated — see AuditEventView."""
    async with session_scope() as session:
        query = select(AuditEvent).order_by(AuditEvent.created_at.desc())
        if category:
            query = query.where(AuditEvent.category == category)
        if action:
            query = query.where(AuditEvent.action == action)
        if actor_email:
            query = query.where(AuditEvent.actor_email.ilike(f"%{actor_email}%"))
        if since:
            query = query.where(AuditEvent.created_at >= since)
        query = query.offset(offset).limit(limit)
        result = await session.execute(query)
        events = list(result.scalars().all())
        return await _to_views(session, events)


def _safe_uuid(value: str | None) -> uuid.UUID | None:
    """Coerce string IDs to UUIDs, tolerating non-UUID values (B-3)."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


async def _to_views(session, events: list[AuditEvent]) -> list[AuditEventView]:
    """Build read-side views with resolved display names. Does not write."""
    from not_dot_net.backend.booking_models import Resource
    from not_dot_net.backend.db import User

    user_ids_to_resolve: set[uuid.UUID] = set()
    resource_ids_to_resolve: set[uuid.UUID] = set()
    for ev in events:
        actor_uuid = _safe_uuid(ev.actor_id)
        if actor_uuid is not None:
            user_ids_to_resolve.add(actor_uuid)
        if ev.target_type == "user":
            tu = _safe_uuid(ev.target_id)
            if tu is not None:
                user_ids_to_resolve.add(tu)
        elif ev.target_type == "resource":
            tr = _safe_uuid(ev.target_id)
            if tr is not None:
                resource_ids_to_resolve.add(tr)

    user_names: dict[str, str] = {}
    if user_ids_to_resolve:
        rows = await session.execute(
            select(User.id, User.email, User.full_name)
            .where(User.id.in_(user_ids_to_resolve))
        )
        for uid, email, full_name in rows:
            user_names[str(uid)] = full_name or email

    resource_names: dict[str, str] = {}
    if resource_ids_to_resolve:
        rows = await session.execute(
            select(Resource.id, Resource.name)
            .where(Resource.id.in_(resource_ids_to_resolve))
        )
        for rid, name in rows:
            resource_names[str(rid)] = name

    views = []
    for ev in events:
        actor_display = ev.actor_email or user_names.get(ev.actor_id or "") or ev.actor_id
        target_display: str | None
        if not ev.target_id:
            target_display = None
        elif ev.target_type == "user":
            target_display = user_names.get(ev.target_id, ev.target_id)
        elif ev.target_type == "resource":
            target_display = resource_names.get(ev.target_id, ev.target_id)
        else:
            target_display = ev.target_id
        views.append(AuditEventView(
            id=ev.id,
            category=ev.category,
            action=ev.action,
            actor_id=ev.actor_id,
            actor_email=ev.actor_email,
            actor_display=actor_display,
            target_type=ev.target_type,
            target_id=ev.target_id,
            target_display=target_display,
            detail=ev.detail,
            metadata_json=ev.metadata_json,
            created_at=ev.created_at,
        ))
    return views
