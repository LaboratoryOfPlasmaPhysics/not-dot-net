"""Whom a computer can be booked for, and over which dates."""

import uuid
from datetime import date, timedelta

from not_dot_net.backend.book_for import book_for_choices, fit_booking_range, presence_of
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import SYSTEM_ACTOR
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.tenure_service import add_tenure
from not_dot_net.backend.workflow_models import RequestStatus, WorkflowRequest
from not_dot_net.config import BookingsConfig

TODAY = date(2026, 10, 13)
CFG = BookingsConfig(minimum_lead_days=7, max_booking_days=183)


async def _user(email, role="staff", **kw) -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role,
                    is_active=True, **kw)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _request(creator, data, type_="onboarding", status=RequestStatus.IN_PROGRESS):
    async with session_scope() as session:
        req = WorkflowRequest(type=type_, current_step="newcomer_info", status=status,
                              data=data, created_by=creator.id, target_email="new@ext.org")
        session.add(req)
        await session.commit()
        await session.refresh(req)
        return req


def test_range_keeps_the_presence_when_it_fits():
    got = fit_booking_range(date(2026, 11, 1), date(2027, 1, 31), TODAY, CFG)
    assert got == {"from": "2026-11-01", "to": "2027-01-31"}


def test_range_starts_no_earlier_than_the_minimum_notice():
    got = fit_booking_range(date(2026, 10, 15), date(2026, 12, 31), TODAY, CFG)
    assert got == {"from": "2026-10-20", "to": "2026-12-31"}


def test_range_is_cut_to_the_maximum_length():
    """A 3-year PhD gets the first max_booking_days; create_booking counts
    the hand-back day, so the last inclusive day is start + max - 1."""
    got = fit_booking_range(date(2026, 11, 1), date(2029, 10, 31), TODAY, CFG)
    assert got == {"from": "2026-11-01", "to": str(date(2026, 11, 1) + timedelta(days=182))}


def test_open_presence_gets_a_week():
    got = fit_booking_range(date(2026, 11, 1), None, TODAY, CFG)
    assert got == {"from": "2026-11-01", "to": "2026-11-08"}


def test_presence_already_over_falls_back_to_the_default():
    got = fit_booking_range(date(2026, 1, 1), date(2026, 6, 30), TODAY, CFG)
    assert got == {"from": "2026-10-20", "to": "2026-10-27"}


async def test_presence_is_the_tenure_covering_today():
    person = await _user("phd@test.com")
    await add_tenure(person.id, "PhD", "CNRS", date(2025, 10, 1), date(2028, 9, 30), actor=SYSTEM_ACTOR)
    assert await presence_of(person.id, TODAY) == (date(2025, 10, 1), date(2028, 9, 30))


async def test_presence_is_else_the_next_tenure():
    person = await _user("intern@test.com")
    await add_tenure(person.id, "Intern", "CNRS", date(2025, 1, 1), date(2025, 6, 30), actor=SYSTEM_ACTOR)
    await add_tenure(person.id, "PhD", "CNRS", date(2027, 1, 1), None, actor=SYSTEM_ACTOR)
    assert await presence_of(person.id, TODAY) == (date(2027, 1, 1), None)


async def test_no_presence_without_tenure():
    person = await _user("ghost@test.com")
    assert await presence_of(person.id, TODAY) is None


async def _roles():
    cfg = await roles_config.get()
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=["create_workflows"])
    cfg.roles["admin"] = RoleDefinition(label="Admin", permissions=["manage_bookings"])
    await roles_config.set(cfg)


async def test_creator_is_offered_their_pending_newcomers_only():
    await _roles()
    boss = await _user("boss@test.com", full_name="Boss")
    other = await _user("other@test.com")
    mine = await _request(boss, {"first_name": "Ada", "last_name": "L",
                                 "start_date": "2026-11-01", "end_date": "2027-04-30"})
    await _request(other, {"start_date": "2026-11-01"})
    await _request(boss, {"start_date": "2026-11-01"}, status=RequestStatus.COMPLETED)
    await _request(boss, {"start_date": "2026-11-01"}, type_="vpn_access")

    choices = await book_for_choices(boss, TODAY)

    assert choices == [{
        "label": "Ada L", "request_id": str(mine.id), "user_id": None,
        "from": "2026-11-01", "to": "2027-04-30",
    }]


async def test_newcomer_without_a_name_shows_their_email():
    await _roles()
    boss = await _user("boss2@test.com")
    await _request(boss, {"start_date": "2026-11-01"})
    [choice] = await book_for_choices(boss, TODAY)
    assert choice["label"] == "new@ext.org"


async def test_booking_manager_is_also_offered_people():
    await _roles()
    manager = await _user("it@test.com", role="admin", full_name="IT")
    phd = await _user("phd2@test.com", full_name="Phd Student")
    await add_tenure(phd.id, "PhD", "CNRS", date(2025, 10, 1), date(2028, 9, 30), actor=SYSTEM_ACTOR)

    choices = await book_for_choices(manager, TODAY)

    by_label = {c["label"]: c for c in choices}
    assert by_label["Phd Student"]["user_id"] == str(phd.id)
    assert by_label["Phd Student"]["from"] == "2025-10-01"
    assert "IT" in by_label


async def test_pending_onboarding_offers_a_booking():
    from not_dot_net.backend.book_for import booking_choice_for
    boss = await _user("boss3@test.com")
    req = await _request(boss, {"start_date": "2026-11-01"})
    choice = await booking_choice_for(req)
    assert choice["request_id"] == str(req.id)
    assert choice["from"] == "2026-11-01"


async def test_other_workflows_and_finished_requests_offer_nothing():
    from not_dot_net.backend.book_for import booking_choice_for
    boss = await _user("boss4@test.com")
    vpn = await _request(boss, {}, type_="vpn_access")
    done = await _request(boss, {}, status=RequestStatus.COMPLETED)
    assert await booking_choice_for(vpn) is None
    assert await booking_choice_for(done) is None
