"""A computer booked for a newcomer while their onboarding is pending.

The booking belongs to whoever made it and is linked to the onboarding
request; rejecting or cancelling the request frees the machine.
"""

import uuid
from datetime import date, timedelta

import pytest

from not_dot_net.backend.booking_models import Booking
from not_dot_net.backend.booking_service import (
    BookingValidationError, create_booking, create_resource,
)
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import SYSTEM_ACTOR
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.workflow_models import RequestStatus, WorkflowRequest
from not_dot_net.backend.workflow_service import cancel_request, delete_request, submit_step
from not_dot_net.config import BookingsConfig


def _start() -> date:
    return date.today() + timedelta(days=BookingsConfig().minimum_lead_days)


async def _user(email, role="staff", **kw) -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role, **kw)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _setup():
    cfg = await roles_config.get()
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=["create_workflows"])
    cfg.roles["admin"] = RoleDefinition(
        label="Admin",
        permissions=["create_workflows", "manage_bookings", "access_personal_data"],
    )
    await roles_config.set(cfg)
    creator = await _user("boss@test.com")
    resource = await create_resource(
        name="PC-1", resource_type="desktop", location="Palaiseau", actor=SYSTEM_ACTOR,
    )
    return creator, resource


async def _request(creator, step="newcomer_info", status=RequestStatus.IN_PROGRESS):
    async with session_scope() as session:
        req = WorkflowRequest(
            type="onboarding", current_step=step, status=status,
            data={"contact_email": "new@ext.org", "status": "PhD", "employer": "CNRS"},
            created_by=creator.id, target_email="new@ext.org",
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
        return req


async def _book(resource, actor, req):
    return await create_booking(
        resource.id, actor.id, _start(), _start() + timedelta(days=5),
        actor=actor, workflow_request_id=req.id,
    )


async def _bookings_of(resource) -> list[Booking]:
    from sqlalchemy import select
    async with session_scope() as session:
        rows = await session.execute(select(Booking).where(Booking.resource_id == resource.id))
        return list(rows.scalars())


async def test_creator_books_for_their_pending_newcomer():
    creator, resource = await _setup()
    req = await _request(creator)
    booking = await _book(resource, creator, req)
    assert booking.user_id == creator.id
    assert booking.workflow_request_id == req.id


async def test_someone_else_cannot_link_a_booking_to_the_request():
    creator, resource = await _setup()
    req = await _request(creator)
    stranger = await _user("stranger@test.com")
    with pytest.raises(PermissionError):
        await _book(resource, stranger, req)


async def test_booking_manager_may_link_to_any_pending_request():
    creator, resource = await _setup()
    req = await _request(creator)
    manager = await _user("it@test.com", role="admin")
    booking = await _book(resource, manager, req)
    assert booking.user_id == manager.id


async def test_finished_request_cannot_get_a_booking():
    creator, resource = await _setup()
    req = await _request(creator, step="done", status=RequestStatus.COMPLETED)
    with pytest.raises(BookingValidationError):
        await _book(resource, creator, req)


async def test_request_linked_booking_is_owned_by_its_maker():
    creator, resource = await _setup()
    req = await _request(creator)
    other = await _user("other@test.com")
    with pytest.raises(PermissionError):
        await create_booking(
            resource.id, other.id, _start(), _start() + timedelta(days=5),
            actor=creator, workflow_request_id=req.id,
        )


async def test_cancelling_the_request_frees_the_computer():
    creator, resource = await _setup()
    req = await _request(creator)
    await _book(resource, creator, req)
    await cancel_request(req.id, creator.id, actor_user=creator)
    assert await _bookings_of(resource) == []


async def test_rejecting_the_request_frees_the_computer():
    creator, resource = await _setup()
    req = await _request(creator, step="admin_validation")
    await _book(resource, creator, req)
    admin = await _user("hr@test.com", role="admin")
    await submit_step(req.id, admin.id, "reject", comment="no", actor_user=admin)
    assert await _bookings_of(resource) == []


async def test_deleting_the_request_keeps_the_booking():
    creator, resource = await _setup()
    req = await _request(creator)
    await _book(resource, creator, req)
    root = await _user("root@test.com", role="admin", is_superuser=True)
    await delete_request(req.id, root)
    [booking] = await _bookings_of(resource)
    assert booking.workflow_request_id is None
