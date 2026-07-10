import uuid
from datetime import date

import pytest
from sqlalchemy import select

from not_dot_net.backend.booking_models import Resource
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.office_availability import OfficeAvailability


async def _create_user(email="owner@test.com") -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _create_office(owner: User | None = None, **kwargs) -> Resource:
    defaults = {"name": "Room 101", "resource_type": "office", "location": "Palaiseau"}
    defaults.update(kwargs)
    async with session_scope() as session:
        resource = Resource(owner_user_id=owner.id if owner else None, **defaults)
        session.add(resource)
        await session.commit()
        await session.refresh(resource)
        return resource


async def test_resource_owner_user_id_defaults_to_none():
    resource = await _create_office()
    assert resource.owner_user_id is None


async def test_resource_owner_user_id_set_null_when_owner_deleted():
    owner = await _create_user()
    resource = await _create_office(owner=owner)
    async with session_scope() as session:
        stored = await session.get(User, owner.id)
        await session.delete(stored)
        await session.commit()
    async with session_scope() as session:
        refreshed = await session.get(Resource, resource.id)
    assert refreshed.owner_user_id is None


async def test_office_availability_cascades_on_resource_delete():
    offeror = await _create_user(email="admin@test.com")
    resource = await _create_office()
    async with session_scope() as session:
        window = OfficeAvailability(
            resource_id=resource.id, start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 15), offered_by=offeror.id,
        )
        session.add(window)
        await session.commit()

    async with session_scope() as session:
        stored = await session.get(Resource, resource.id)
        await session.delete(stored)
        await session.commit()

    async with session_scope() as session:
        remaining = (await session.execute(select(OfficeAvailability))).scalars().all()
    assert remaining == []


async def test_office_availability_offered_by_fk_enforced():
    resource = await _create_office()
    async with session_scope() as session:
        window = OfficeAvailability(
            resource_id=resource.id, start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 15), offered_by=uuid.uuid4(),
        )
        session.add(window)
        with pytest.raises(Exception):
            await session.commit()
