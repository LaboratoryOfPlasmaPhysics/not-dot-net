"""?tab= query-param deep-linking in the main shell page.

When a link like `/?tab=bookings` is opened, the bookings tab must be selected
as the initial tab. Unknown keys must silently fall back to the dashboard.
"""

from contextlib import asynccontextmanager

from nicegui import ElementFilter, ui
from nicegui.testing import User

from not_dot_net.backend.db import User as DbUser, get_user_db, session_scope
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.backend.users import get_jwt_strategy, get_user_manager
from not_dot_net.frontend.i18n import t


async def _login(user: User, email: str = "deeplink@not-dot-net.dev") -> DbUser:
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                from fastapi_users.exceptions import UserAlreadyExists

                try:
                    db_user = await manager.create(
                        UserCreate(email=email, password="secret-pw")
                    )
                except UserAlreadyExists:
                    db_user = await manager.get_by_email(email)
                token = await get_jwt_strategy().write_token(db_user)
    user.http_client.cookies.set("fastapiusersauth", token)
    return db_user


async def test_tab_query_param_selects_bookings(user: User) -> None:
    await _login(user)
    await user.open("/?tab=bookings")
    # Wait for the bookings panel content to render (timer-deferred).
    await user.should_see(t("resources"))
    with user.client:
        panels_el = next(el for el in ElementFilter(kind=ui.tab_panels))
        assert panels_el.value == "bookings", (
            f"Expected bookings tab to be selected, got {panels_el.value!r}"
        )


async def test_unknown_tab_falls_back_to_dashboard(user: User) -> None:
    await _login(user, email="deeplink-fallback@not-dot-net.dev")
    await user.open("/?tab=does-not-exist")
    # Wait for the dashboard panel content to render (timer-deferred).
    await user.should_see(t("no_requests"))
    with user.client:
        panels_el = next(el for el in ElementFilter(kind=ui.tab_panels))
        assert panels_el.value == "dashboard", (
            f"Expected dashboard tab on unknown key, got {panels_el.value!r}"
        )
