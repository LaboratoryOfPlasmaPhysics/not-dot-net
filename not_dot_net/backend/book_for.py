"""Whom a computer can be booked for, and over which dates.

A choice is a plain dict so it can sit in app.storage.user between the
"Book a computer" button and the booking form.
"""

import uuid
from datetime import date, timedelta

from sqlalchemy import or_, select

from not_dot_net.backend.booking_service import MANAGE_BOOKINGS
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.tenure_service import UserTenure
from not_dot_net.backend.workflow_config import workflows_config
from not_dot_net.backend.workflow_models import RequestStatus, WorkflowRequest
from not_dot_net.config import BookingsConfig, TenureHookConfig

OPEN_PRESENCE_DAYS = 7


def fit_booking_range(start: date, end: date | None, today: date, cfg: BookingsConfig) -> dict[str, str]:
    """The presence period cut to what create_booking accepts. Ends are inclusive."""
    earliest = today + timedelta(days=cfg.minimum_lead_days)
    start = max(start, earliest)
    end = end or start + timedelta(days=OPEN_PRESENCE_DAYS)
    if end < start:
        start, end = earliest, earliest + timedelta(days=OPEN_PRESENCE_DAYS)
    end = min(end, start + timedelta(days=cfg.max_booking_days - 1))
    return {"from": str(start), "to": str(end)}


async def presence_of(user_id: uuid.UUID, today: date) -> tuple[date, date | None] | None:
    """The tenure covering today, else the next one to start."""
    async with session_scope() as session:
        rows = await session.execute(
            select(UserTenure)
            .where(UserTenure.user_id == user_id,
                   or_(UserTenure.end_date == None, UserTenure.end_date >= today))  # noqa: E711
            .order_by(UserTenure.start_date)
        )
    tenures = list(rows.scalars())
    current = [t for t in tenures if t.start_date <= today]
    chosen = current[-1] if current else (tenures[0] if tenures else None)
    return (chosen.start_date, chosen.end_date) if chosen else None


def _iso(value) -> str | None:
    try:
        return date.fromisoformat(value or "").isoformat()
    except (TypeError, ValueError):
        return None


def _request_choice(req: WorkflowRequest, hook: TenureHookConfig) -> dict:
    name = " ".join(filter(None, (req.data.get("first_name"), req.data.get("last_name"))))
    return {
        "label": name or req.target_email or str(req.id),
        "request_id": str(req.id), "user_id": None,
        "from": _iso(req.data.get(hook.start_date_field)),
        "to": _iso(req.data.get(hook.end_date_field)),
    }


async def booking_choice_for(req: WorkflowRequest) -> dict | None:
    """What the "Book a computer" button of this request preselects, if any."""
    wf = (await workflows_config.get()).workflows.get(req.type)
    if wf is None or not wf.computer_booking or req.status != RequestStatus.IN_PROGRESS:
        return None
    return _request_choice(req, wf.tenure or TenureHookConfig())


async def request_choices(actor, is_manager: bool) -> list[dict]:
    """Pending requests of booking-enabled workflows the actor may book for."""
    workflows = {k: wf for k, wf in (await workflows_config.get()).workflows.items()
                 if wf.computer_booking}
    if not workflows:
        return []
    query = select(WorkflowRequest).where(
        WorkflowRequest.type.in_(workflows), WorkflowRequest.status == RequestStatus.IN_PROGRESS,
    ).order_by(WorkflowRequest.created_at)
    if not is_manager:
        query = query.where(WorkflowRequest.created_by == actor.id)
    async with session_scope() as session:
        requests = list((await session.execute(query)).scalars())
    return [_request_choice(r, workflows[r.type].tenure or TenureHookConfig()) for r in requests]


async def _person_choice(user: User, today: date) -> dict:
    presence = await presence_of(user.id, today)
    start, end = presence or (None, None)
    return {
        "label": user.full_name or user.email,
        "request_id": None, "user_id": str(user.id),
        "from": start and str(start), "to": end and str(end),
    }


async def book_for_choices(actor, today: date) -> list[dict]:
    # simplify: one tenure query per person, fine for a lab-sized directory;
    # batch it into one query if the list gets slow.
    is_manager = await has_permissions(actor, MANAGE_BOOKINGS)
    choices = await request_choices(actor, is_manager)
    if is_manager:
        async with session_scope() as session:
            people = (await session.execute(
                select(User).where(User.is_active == True).order_by(User.full_name, User.email)  # noqa: E712
            )).scalars().all()
        choices += [await _person_choice(p, today) for p in people]
    return choices
