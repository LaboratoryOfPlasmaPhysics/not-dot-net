"""The "Book for" picker and the "Book a computer" button."""

import uuid

from nicegui import app, ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.workflow_models import RequestStatus, WorkflowRequest
from not_dot_net.frontend.bookings import _booking_owner, _render_bookings


async def _setup():
    cfg = await roles_config.get()
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=["create_workflows"])
    await roles_config.set(cfg)
    async with session_scope() as session:
        boss = User(id=uuid.uuid4(), email="boss@test.com", hashed_password="x",
                    role="staff", is_active=True)
        session.add(boss)
        await session.commit()
        await session.refresh(boss)
        req = WorkflowRequest(
            type="onboarding", current_step="newcomer_info", status=RequestStatus.IN_PROGRESS,
            data={"first_name": "Ada", "last_name": "Lovelace",
                  "start_date": "2099-11-01", "end_date": "2100-01-31"},
            created_by=boss.id, target_email="ada@ext.org",
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
    return boss, req


def test_newcomer_booking_is_held_by_its_maker_with_their_name():
    me = uuid.uuid4()
    rid = uuid.uuid4()
    target = {"label": "Ada Lovelace", "request_id": str(rid), "user_id": None}
    assert _booking_owner(target, me) == (me, rid, "Ada Lovelace")


def test_person_booking_belongs_to_that_person():
    me, them = uuid.uuid4(), uuid.uuid4()
    target = {"label": "Bob", "request_id": None, "user_id": str(them)}
    assert _booking_owner(target, me) == (them, None, "")


def test_no_target_books_for_myself():
    me = uuid.uuid4()
    assert _booking_owner(None, me) == (me, None, "")


async def test_picking_a_newcomer_sets_their_dates(user: UiUser):
    boss, req = await _setup()

    @ui.page("/_book_for")
    async def page():
        await _render_bookings(ui.column(), boss)

    await user.open("/_book_for")
    user.find("Book for").elements.pop().set_value(str(req.id))
    await user.should_see("Booking for Ada Lovelace")
    await user.should_see("2099-11-01 → 2100-01-31")
    with user.client:
        assert app.storage.user["book_for"]["request_id"] == str(req.id)


async def test_stored_target_is_shown_on_arrival(user: UiUser):
    boss, req = await _setup()

    @ui.page("/_book_for_stored")
    async def page():
        app.storage.user["book_for"] = {
            "label": "Ada Lovelace", "request_id": str(req.id), "user_id": None,
            "from": "2099-11-01", "to": "2100-01-31",
        }
        await _render_bookings(ui.column(), boss)

    await user.open("/_book_for_stored")
    await user.should_see("Booking for Ada Lovelace")
    await user.should_see("2099-11-01 → 2100-01-31")
