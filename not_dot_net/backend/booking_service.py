"""Booking service — resource CRUD and reservation management."""

import logging
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from not_dot_net.backend.booking_models import Booking, Resource, ResourceStatus
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.mail import send_mail
from not_dot_net.backend.office_availability import is_covered, list_availability_windows
from not_dot_net.backend.permissions import check_permission, has_permissions, permission
from not_dot_net.config import bookings_config, org_config

MANAGE_BOOKINGS = permission("manage_bookings", "Manage bookings", "Create/edit/delete resources and software")

ALLOWED_TRANSITIONS: dict[ResourceStatus, set[ResourceStatus]] = {
    ResourceStatus.AVAILABLE:      {ResourceStatus.BOOKED, ResourceStatus.READY, ResourceStatus.OUT_OF_SERVICE},
    ResourceStatus.BOOKED:         {ResourceStatus.READY, ResourceStatus.AVAILABLE, ResourceStatus.OUT_OF_SERVICE},
    ResourceStatus.READY:          {ResourceStatus.IN_USE, ResourceStatus.AVAILABLE, ResourceStatus.OUT_OF_SERVICE},
    ResourceStatus.IN_USE:         {ResourceStatus.RETURNED, ResourceStatus.OUT_OF_SERVICE},
    ResourceStatus.RETURNED:       {ResourceStatus.AVAILABLE, ResourceStatus.OUT_OF_SERVICE},
    ResourceStatus.OUT_OF_SERVICE: {ResourceStatus.AVAILABLE},
}


def available_transitions(status: str) -> list[str]:
    """Legal next status values from the given status, sorted for stable UI.
    Unknown (legacy/corrupt) statuses yield no transitions instead of crashing."""
    try:
        nexts = ALLOWED_TRANSITIONS[ResourceStatus(status)]
    except ValueError:
        return []
    return sorted(s.value for s in nexts)


logger = logging.getLogger("not_dot_net.booking_service")


class BookingConflictError(Exception):
    pass


class BookingValidationError(Exception):
    pass


# --- Resources ---


async def list_resources(active_only: bool = True) -> list[Resource]:
    async with session_scope() as session:
        query = select(Resource).order_by(Resource.name)
        if active_only:
            query = query.where(Resource.active == True)  # noqa: E712
        result = await session.execute(query)
        return list(result.scalars().all())


async def create_resource(name: str, resource_type: str, description: str = "",
                          location: str = "", specs: dict | None = None,
                          owner_user_id: uuid.UUID | None = None,
                          actor=None) -> Resource:
    if actor is not None:
        await check_permission(actor, MANAGE_BOOKINGS)
    async with session_scope() as session:
        resource = Resource(
            name=name,
            resource_type=resource_type,
            description=description or None,
            location=location or None,
            specs=specs,
            owner_user_id=owner_user_id,
        )
        session.add(resource)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ValueError(f"Resource name '{name}' already exists") from exc
        await session.refresh(resource)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "resource", "create",
        target_type="resource", target_id=resource.id,
        detail=f"name={name} type={resource_type}",
    )
    return resource


_RESOURCE_MUTABLE = frozenset({"name", "resource_type", "description", "location", "specs", "active", "owner_user_id"})


async def update_resource(resource_id: uuid.UUID, actor=None, **kwargs) -> Resource:
    if actor is not None:
        await check_permission(actor, MANAGE_BOOKINGS)
    async with session_scope() as session:
        resource = await session.get(Resource, resource_id)
        if resource is None:
            raise ValueError(f"Resource {resource_id} not found")
        for key, value in kwargs.items():
            if key not in _RESOURCE_MUTABLE:
                raise ValueError(f"Cannot update field '{key}'")
            setattr(resource, key, value)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ValueError("Resource update violates a uniqueness constraint") from exc
        await session.refresh(resource)
        return resource


async def delete_resource(resource_id: uuid.UUID, actor=None) -> None:
    if actor is not None:
        await check_permission(actor, MANAGE_BOOKINGS)
    async with session_scope() as session:
        resource = await session.get(Resource, resource_id)
        if resource is None:
            raise ValueError(f"Resource {resource_id} not found")
        if resource.active:
            raise BookingValidationError("Retire the resource before deleting it")
        upcoming = await session.execute(
            select(Booking).where(
                Booking.resource_id == resource_id,
                Booking.end_date >= date.today(),
            ).limit(1)
        )
        if upcoming.scalars().first() is not None:
            # The FK would CASCADE-delete them silently; force an explicit choice.
            raise BookingValidationError(
                "Resource has upcoming bookings — migrate or cancel them first"
            )
        deleted_name = resource.name
        await session.delete(resource)
        await session.commit()

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "resource", "delete",
        actor_id=(actor.id if actor else None),
        target_type="resource", target_id=resource_id,
        detail=f"name={deleted_name}",
    )


async def restore_resource(resource_id: uuid.UUID, actor=None) -> Resource:
    """Un-retire a resource. Forces status back to AVAILABLE, deliberately
    bypassing the FSM: the resource may have been retired from any state, and
    whatever physical state it held is stale after sitting out of the pool."""
    if actor is not None:
        await check_permission(actor, MANAGE_BOOKINGS)
    async with session_scope() as session:
        resource = await session.get(Resource, resource_id)
        if resource is None:
            raise ValueError(f"Resource {resource_id} not found")
        resource.active = True
        resource.status = ResourceStatus.AVAILABLE.value
        await session.commit()
        await session.refresh(resource)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "resource", "restore",
        actor_id=(actor.id if actor else None),
        target_type="resource", target_id=resource_id,
        detail="",
    )
    return resource


_STATUS_NOTICE: dict[ResourceStatus, tuple[str, str]] = {
    ResourceStatus.READY: (
        "Your resource is ready for pickup",
        "Your booked resource is ready for pickup.",
    ),
    ResourceStatus.IN_USE: (
        "Resource pickup confirmed",
        "We've recorded that you picked up your booked resource.",
    ),
    ResourceStatus.RETURNED: (
        "Resource return confirmed",
        "We've recorded the return of your booked resource. Thank you.",
    ),
}


def _resource_status_context(*, user, resource, subject_line, headline, bookings_url) -> dict:
    return {
        "recipient_name": user.full_name or user.email,
        "subject_line": subject_line,
        "headline": headline,
        "resource_name": resource.name,
        "location": resource.location or "-",
        "bookings_url": bookings_url,
    }


async def _current_booking_user(session, resource_id: uuid.UUID, today: date) -> User | None:
    """The user of the active booking (start ≤ today ≤ end — end_date is the
    hand-back day, so its user is still current then); else the nearest
    upcoming booking; else None."""
    result = await session.execute(
        select(Booking, User)
        .join(User, Booking.user_id == User.id)
        .where(Booking.resource_id == resource_id, Booking.end_date >= today)
        .order_by(Booking.start_date)
    )
    rows = list(result.all())
    for booking, user in rows:
        if booking.start_date <= today:
            return user
    return rows[0][1] if rows else None


async def _out_of_service_recipients(session) -> list[User]:
    result = await session.execute(select(User).where(User.is_active == True))  # noqa: E712
    users = list(result.scalars().all())
    return [u for u in users if u.is_superuser or await has_permissions(u, MANAGE_BOOKINGS)]


async def _booking_email_env() -> tuple[str, str, str]:
    cfg = await org_config.get()
    app_name = (cfg.app_name or "not-dot-net").strip() or "not-dot-net"
    base_url = cfg.base_url.rstrip("/")
    return app_name, f"{base_url}/?tab=bookings", f"{base_url}/"


async def _notify_status_change(resource: Resource, new_status: ResourceStatus,
                                today: date) -> None:
    from not_dot_net.backend.email_templates import render_email
    app_name, bookings_url, app_url = await _booking_email_env()
    async with session_scope() as session:
        booking_user = await _current_booking_user(session, resource.id, today)

        if new_status in _STATUS_NOTICE:
            if booking_user is None or not booking_user.email:
                return
            subject_line, headline = _STATUS_NOTICE[new_status]
            ctx = _resource_status_context(user=booking_user, resource=resource,
                                           subject_line=subject_line, headline=headline,
                                           bookings_url=bookings_url)
            ctx["app_name"] = app_name
            ctx["app_url"] = app_url
            subject, body = await render_email("resource_status", ctx)
            await send_mail(booking_user.email, subject, body)
            return

        if new_status is ResourceStatus.OUT_OF_SERVICE:
            targets = await _out_of_service_recipients(session)
            if booking_user is not None:
                targets.append(booking_user)
            seen: set[str] = set()
            headline = f"{resource.name} has been marked out of service."
            for u in targets:
                if not u.email or u.email in seen:
                    continue
                seen.add(u.email)
                ctx = _resource_status_context(user=u, resource=resource,
                                               subject_line="", headline=headline,
                                               bookings_url=bookings_url)
                ctx["app_name"] = app_name
                ctx["app_url"] = app_url
                subject, body = await render_email("resource_out_of_service", ctx)
                await send_mail(u.email, subject, body)


async def set_resource_status(resource_id: uuid.UUID, new_status, actor=None,
                              today: date | None = None) -> Resource:
    if actor is not None:
        await check_permission(actor, MANAGE_BOOKINGS)
    target = ResourceStatus(new_status)
    today = today or date.today()
    async with session_scope() as session:
        resource = await session.get(Resource, resource_id, with_for_update=True)
        if resource is None:
            raise ValueError(f"Resource {resource_id} not found")
        current = ResourceStatus(resource.status)
        if target not in ALLOWED_TRANSITIONS[current]:
            raise BookingValidationError(
                f"Cannot change status from {current.value} to {target.value}"
            )
        old = resource.status
        resource.status = target.value
        await session.commit()
        await session.refresh(resource)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "resource", "status",
        actor_id=(actor.id if actor else None),
        target_type="resource", target_id=resource_id,
        detail=f"{old}→{target.value}",
    )
    await _notify_status_change(resource, target, today)  # implemented in Task 3
    return resource


# --- Bookings ---


async def list_bookings_for_resource(
    resource_id: uuid.UUID, from_date: date | None = None, to_date: date | None = None,
) -> list[Booking]:
    async with session_scope() as session:
        query = (
            select(Booking)
            .where(Booking.resource_id == resource_id)
            .order_by(Booking.start_date)
        )
        if from_date:
            query = query.where(Booking.end_date >= from_date)
        if to_date:
            query = query.where(Booking.start_date <= to_date)
        result = await session.execute(query)
        return list(result.scalars().all())


async def list_bookings_for_user(user_id: uuid.UUID) -> list[Booking]:
    async with session_scope() as session:
        result = await session.execute(
            select(Booking)
            .where(Booking.user_id == user_id, Booking.end_date >= date.today())
            .order_by(Booking.start_date)
        )
        return list(result.scalars().all())


async def create_booking(
    resource_id: uuid.UUID, user_id: uuid.UUID,
    start_date: date, end_date: date, note: str = "",
    os_choice: str | None = None, software_tags: list[str] | None = None,
    actor=None,
) -> Booking:
    if actor is not None:
        is_manager = await has_permissions(actor, MANAGE_BOOKINGS)
        if user_id != actor.id and not is_manager:
            raise PermissionError("Can only create bookings for yourself")
    if start_date >= end_date:
        raise BookingValidationError("End date must be after start date")
    if start_date < date.today():
        raise BookingValidationError("Cannot book in the past")
    cfg = await bookings_config.get()
    minimum_lead_days = cfg.minimum_lead_days
    earliest_start = date.today() + timedelta(days=minimum_lead_days)
    if start_date < earliest_start:
        raise BookingValidationError(
            f"Bookings must start at least {minimum_lead_days} days from today"
        )
    max_booking_days = cfg.max_booking_days
    if (end_date - start_date).days > max_booking_days:
        raise BookingValidationError(f"Booking cannot exceed {max_booking_days} days")
    setup_buffer_days = cfg.resource_setup_buffer_days

    async with session_scope() as session:
        async with session.begin():
            # Lock the resource row so concurrent create_booking calls serialize:
            # FOR UPDATE on the conflicts query alone can't stop a phantom insert
            # when no conflicting rows exist yet. No-op on SQLite, real on PostgreSQL.
            resource = await session.get(Resource, resource_id, with_for_update=True)
            if resource is None:
                raise ValueError(f"Resource {resource_id} not found")
            if not resource.active:
                raise BookingValidationError("Resource is not active")

            if resource.resource_type == "office":
                windows = await list_availability_windows(resource_id)
                if not is_covered(windows, start_date, end_date):
                    raise BookingValidationError(
                        "Requested dates are outside the offered availability window"
                    )

            conflicts = await session.execute(
                select(Booking).where(
                    Booking.resource_id == resource_id,
                    Booking.start_date < end_date + timedelta(days=setup_buffer_days),
                    Booking.end_date > start_date - timedelta(days=setup_buffer_days),
                )
            )
            if conflicts.scalars().first():
                raise BookingConflictError(
                    f"This resource is already booked or within the {setup_buffer_days}-day setup buffer"
                )

            booking = Booking(
                resource_id=resource_id,
                user_id=user_id,
                start_date=start_date,
                end_date=end_date,
                os_choice=os_choice,
                software_tags=software_tags or None,
                note=note or None,
            )
            session.add(booking)
        await session.refresh(booking)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "booking", "create",
        actor_id=(actor.id if actor is not None else user_id),
        target_type="resource", target_id=resource_id,
        detail=f"{start_date} → {end_date}",
    )
    return booking


async def cancel_booking(booking_id: uuid.UUID, actor=None) -> None:
    async with session_scope() as session:
        booking = await session.get(Booking, booking_id)
        if booking is None:
            raise ValueError("Booking not found")

        if actor is None:
            raise PermissionError("No actor provided")
        is_owner = booking.user_id == actor.id
        if not is_owner and not await has_permissions(actor, MANAGE_BOOKINGS):
            raise PermissionError("Can only cancel your own bookings")

        resource_id = booking.resource_id
        await session.delete(booking)
        await session.commit()

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "booking", "cancel",
        actor_id=actor.id,
        target_type="resource", target_id=resource_id,
        detail=f"booking={booking_id}",
    )


async def migrate_booking(booking_id: uuid.UUID, new_resource_id: uuid.UUID,
                          actor=None) -> Booking:
    """Move a booking to another resource (e.g. before deleting the old one).

    Conflict-checked against the target with the same setup buffer as
    create_booking; the booking's user is notified by email."""
    if actor is not None:
        await check_permission(actor, MANAGE_BOOKINGS)
    cfg = await bookings_config.get()
    buffer_days = timedelta(days=cfg.resource_setup_buffer_days)
    async with session_scope() as session:
        async with session.begin():
            booking = await session.get(Booking, booking_id)
            if booking is None:
                raise ValueError("Booking not found")
            if booking.resource_id == new_resource_id:
                raise BookingValidationError("Booking is already on this resource")
            # Same lock as create_booking: serialize bookings per target resource.
            target = await session.get(Resource, new_resource_id, with_for_update=True)
            if target is None:
                raise ValueError(f"Resource {new_resource_id} not found")
            if not target.active:
                raise BookingValidationError("Target resource is not active")
            old_resource = await session.get(Resource, booking.resource_id)
            conflicts = await session.execute(
                select(Booking).where(
                    Booking.resource_id == new_resource_id,
                    Booking.id != booking.id,
                    Booking.start_date < booking.end_date + buffer_days,
                    Booking.end_date > booking.start_date - buffer_days,
                )
            )
            if conflicts.scalars().first():
                raise BookingConflictError(
                    f"'{target.name}' is already booked or within the setup buffer in that period"
                )
            user = await session.get(User, booking.user_id)
            old_name = old_resource.name if old_resource else "?"
            booking.resource_id = new_resource_id
        await session.refresh(booking)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "booking", "migrate",
        actor_id=(actor.id if actor else None),
        target_type="resource", target_id=new_resource_id,
        detail=f"booking={booking_id} {old_name} → {target.name}",
    )
    await _notify_booking_migrated(booking, old_name, target, user)
    return booking


async def _notify_booking_migrated(booking: Booking, old_name: str,
                                   target: Resource, user: User | None) -> None:
    if user is None or not user.email:
        return
    from not_dot_net.backend.email_templates import render_email
    app_name, bookings_url, app_url = await _booking_email_env()
    ctx = {
        "recipient_name": user.full_name or user.email,
        "old_resource_name": old_name,
        "new_resource_name": target.name,
        "location": target.location or "-",
        "start_date": booking.start_date.isoformat(),
        "last_day": booking_last_day(booking.end_date).isoformat(),
        "bookings_url": bookings_url,
        "app_name": app_name,
        "app_url": app_url,
    }
    subject, body = await render_email("booking_migrated", ctx)
    await send_mail(user.email, subject, body)


async def get_resource_by_id(resource_id: uuid.UUID) -> Resource | None:
    async with session_scope() as session:
        return await session.get(Resource, resource_id)


# --- Booking reminders ---


def booking_last_day(end_date: date) -> date:
    """Bookings store an exclusive end (hand-back day); users think in
    inclusive last-usage days. All user-facing dates use this."""
    return end_date - timedelta(days=1)


def _booking_reminder_delay_label(days_until_end: int) -> str:
    if days_until_end == 0:
        return "today"
    if days_until_end == 1:
        return "in 1 day"
    return f"in {days_until_end} days"


def _booking_reminder_context(*, user, booking, resource, delay_label, bookings_url) -> dict:
    return {
        "recipient_name": user.full_name or user.email,
        "delay_label": delay_label,
        "resource_name": resource.name,
        "start_date": str(booking.start_date),
        "end_date": str(booking_last_day(booking.end_date)),
        "os": booking.os_choice or "-",
        "software": ", ".join(booking.software_tags or []) or "-",
        "bookings_url": bookings_url,
    }


async def send_booking_end_reminders(today: date | None = None) -> int:
    """Queue reminder emails for bookings ending on configured lead days.

    Returns the number of reminder emails queued. Each booking stores the lead
    days already notified to avoid duplicate mails across multiple reminders.
    """
    today = today or date.today()
    lead_days = (await bookings_config.get()).reminder_lead_days
    if not lead_days:
        return 0
    # end_date is exclusive: a last usage day `lead` days out means
    # end_date = today + lead + 1.
    latest_end = today + timedelta(days=max(lead_days) + 1)
    queued = 0

    async with session_scope() as session:
        result = await session.execute(
            select(Booking, User, Resource)
            .join(User, Booking.user_id == User.id)
            .join(Resource, Booking.resource_id == Resource.id)
            .where(
                Booking.end_date >= today,
                Booking.end_date <= latest_end,
                User.is_active.is_(True),
                User.email.is_not(None),
                User.email != "",
            )
            .order_by(Booking.end_date, Resource.name)
        )
        rows = list(result.all())

        from not_dot_net.backend.email_templates import render_email
        app_name, bookings_url, app_url = await _booking_email_env()
        for booking, user, resource in rows:
            days_until_end = (booking_last_day(booking.end_date) - today).days
            if days_until_end < 0:
                continue
            sent_lead_days = set(booking.reminder_sent_lead_days or [])
            # Catch up on lead days the job missed (e.g. it wasn't running on
            # the exact day) — one email covers every lead that has passed.
            due_leads = [
                lead for lead in lead_days
                if days_until_end <= lead and lead not in sent_lead_days
            ]
            if not due_leads:
                continue
            delay_label = _booking_reminder_delay_label(days_until_end)
            ctx = _booking_reminder_context(user=user, booking=booking, resource=resource,
                                            delay_label=delay_label, bookings_url=bookings_url)
            ctx["app_name"] = app_name
            ctx["app_url"] = app_url
            subject, body = await render_email("booking_reminder", ctx)
            await send_mail(user.email, subject, body)
            sent_lead_days.update(due_leads)
            booking.reminder_sent_lead_days = sorted(sent_lead_days)
            await session.commit()
            queued += 1

    return queued


async def run_booking_reminder_job() -> None:
    """APScheduler entrypoint for booking reminder emails."""
    try:
        queued = await send_booking_end_reminders()
        if queued:
            logger.info("Queued %d booking reminder email(s)", queued)
    except Exception:
        logger.exception("Booking reminder job failed")
