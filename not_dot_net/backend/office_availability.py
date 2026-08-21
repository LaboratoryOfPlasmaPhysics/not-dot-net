"""Office availability — owner-declared booking windows for office resources.

Kept separate from booking_service.py: this module owns windows and
ownership; booking_service.py owns reservations and conflicts. The only
integration point is create_booking's is_covered() check.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, func, select
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.booking_models import Booking, Resource
from not_dot_net.backend.db import Base, session_scope
from not_dot_net.backend.permissions import has_permissions


class OfficeAvailability(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "office_availability"

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource.id", ondelete="CASCADE"), index=True
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    offered_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE")
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)


def is_covered(windows: list[OfficeAvailability], start_date: date, end_date: date) -> bool:
    """True if [start_date, end_date) is fully covered by the union of the
    given windows. Pure/no DB — overlapping/duplicate windows are absorbed
    for free by this sweep."""
    day = start_date
    for window in sorted(windows, key=lambda w: w.start_date):
        if window.start_date > day:
            break
        if window.end_date > day:
            day = window.end_date
    return day >= end_date


class OfficeAvailabilityError(Exception):
    pass


async def _check_owner_or_manager(resource: Resource, actor) -> None:
    if actor is None:
        raise PermissionError("No actor provided")
    is_owner = actor.id == resource.owner_user_id
    if not is_owner and not await has_permissions(actor, "manage_bookings"):
        raise PermissionError("Only the office owner or a booking manager can do this")


async def offer_availability(
    resource_id: uuid.UUID, start_date: date, end_date: date, actor=None,
) -> OfficeAvailability:
    if start_date >= end_date:
        raise OfficeAvailabilityError("End date must be after start date")
    async with session_scope() as session:
        resource = await session.get(Resource, resource_id)
        if resource is None:
            raise ValueError(f"Resource {resource_id} not found")
        await _check_owner_or_manager(resource, actor)
        window = OfficeAvailability(
            resource_id=resource_id, start_date=start_date, end_date=end_date,
            offered_by=actor.id,
        )
        session.add(window)
        await session.commit()
        await session.refresh(window)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "office_availability", "offer",
        actor_id=actor.id,
        target_type="resource", target_id=resource_id,
        detail=f"{start_date} → {end_date}",
    )
    return window


async def list_availability_windows(resource_id: uuid.UUID) -> list[OfficeAvailability]:
    async with session_scope() as session:
        result = await session.execute(
            select(OfficeAvailability)
            .where(OfficeAvailability.resource_id == resource_id)
            .order_by(OfficeAvailability.start_date)
        )
        return list(result.scalars().all())


async def revoke_availability(window_id: uuid.UUID, actor=None) -> None:
    async with session_scope() as session:
        window = await session.get(OfficeAvailability, window_id)
        if window is None:
            raise ValueError(f"Availability window {window_id} not found")
        # Same lock create_booking takes: without it a booking can commit
        # against this window between the check below and the DELETE.
        resource = await session.get(Resource, window.resource_id, with_for_update=True)
        await _check_owner_or_manager(resource, actor)

        conflicting = await session.execute(
            select(Booking).where(
                Booking.resource_id == window.resource_id,
                Booking.start_date < window.end_date,
                Booking.end_date > window.start_date,
            ).limit(1)
        )
        if conflicting.scalars().first() is not None:
            raise OfficeAvailabilityError(
                "A booking falls within this window — cancel it before revoking"
            )
        resource_id = window.resource_id
        await session.delete(window)
        await session.commit()

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "office_availability", "revoke",
        actor_id=actor.id,
        target_type="resource", target_id=resource_id,
        detail=f"window={window_id}",
    )
