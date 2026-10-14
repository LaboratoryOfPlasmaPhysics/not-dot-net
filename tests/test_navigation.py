"""E1: everyday places in the bar, admin places in one menu."""

import asyncio
from contextlib import asynccontextmanager

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import User, get_user_db, session_scope
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.backend.users import get_jwt_strategy, get_user_manager

EVERYDAY = ["dashboard", "services", "people", "bookings", "floorplan", "pages"]


async def _log_in(user: UiUser, email: str, *, superuser: bool) -> None:
    await user.open("/login")
    await asyncio.sleep(0.2)  # let startup tasks finish
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                account = await manager.create(UserCreate(email=email, password="pw123456"))
        if superuser:
            (await session.get(User, account.id)).is_superuser = True
            await session.commit()
    user.http_client.cookies.set("fastapiusersauth", await get_jwt_strategy().write_token(account))


def _bar_tabs(user: UiUser) -> list[str]:
    tabs = next(iter(user.find(kind=ui.tabs).elements))
    return [child._props["name"] for child in tabs.default_slot.children]


def _in_a_menu(element) -> bool:
    while element.parent_slot is not None:
        element = element.parent_slot.parent
        if isinstance(element, ui.menu):
            return True
    return False


async def test_admin_places_live_in_one_menu(user: UiUser):
    await _log_in(user, "nav-root@test.com", superuser=True)
    await user.open("/")
    await user.should_see("Admin")
    assert _bar_tabs(user) == EVERYDAY
    for label in ["Operations", "Users", "AD Accounts", "Audit Log", "Settings"]:
        assert any(_in_a_menu(e) for e in user.find(label).elements), label


async def test_member_sees_no_admin_menu(user: UiUser):
    await _log_in(user, "nav-member@test.com", superuser=False)
    await user.open("/")
    await user.should_see("Dashboard")
    await user.should_not_see("Admin")
    assert _bar_tabs(user) == EVERYDAY


async def test_deep_links_to_admin_places_still_work(user: UiUser):
    await _log_in(user, "nav-deep@test.com", superuser=True)
    await user.open("/?tab=audit")
    panels = next(iter(user.find(kind=ui.tab_panels).elements))
    assert panels.value == "audit"


async def test_new_request_is_a_button_on_the_dashboard(user: UiUser):
    await _log_in(user, "nav-new@test.com", superuser=True)
    await user.open("/")
    await user.should_see("New Request")
    assert "new_request" not in _bar_tabs(user)
