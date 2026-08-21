"""Reproducers: tab panels are rendered once at page load and never again,
so data created after the page loaded stays invisible until a browser refresh.
Switching to a tab must re-render that tab's content with fresh data."""

from not_dot_net.backend.permissions import SYSTEM_ACTOR
from contextlib import asynccontextmanager

from nicegui import ElementFilter, ui
from nicegui.testing import User

from not_dot_net.backend.db import User as DbUser, get_user_db, session_scope
from not_dot_net.backend.page_service import create_page, delete_page, get_page
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.backend.users import get_jwt_strategy, get_user_manager
from not_dot_net.frontend.i18n import t


async def _login(
    user: User, email: str = "tab-refresh@not-dot-net.dev", role: str = "",
) -> DbUser:
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
    if role:
        async with session_scope() as session:
            row = await session.get(DbUser, db_user.id)
            row.role = role
            await session.commit()
    user.http_client.cookies.set("fastapiusersauth", token)
    return db_user


async def _ensure_no_page(slug: str) -> None:
    existing = await get_page(slug)
    if existing is not None:
        await delete_page(existing.id, actor=SYSTEM_ACTOR)


async def test_pages_tab_shows_page_created_after_page_load(user: User) -> None:
    await _ensure_no_page("fresh-tab-page")
    await _login(user)
    await user.open("/")
    # Wait for the deferred (timer-based) initial renders to complete:
    # the empty pages list shows its empty state, the empty requests
    # table shows "no requests". Only then is the snapshot truly taken.
    await user.should_see(t("pages_empty"))
    await user.should_see(t("no_requests"))

    await create_page(
        title="Fresh Tab Page", slug="fresh-tab-page",
        content="created after the shell rendered",
        author_id=None, published=True, actor=SYSTEM_ACTOR
    )

    user.find(t("pages"), kind=ui.tab).click()
    await user.should_see("Fresh Tab Page")


async def test_dashboard_shows_data_created_after_page_load_when_switching_back(
    user: User,
) -> None:
    await _ensure_no_page("fresh-dash-page")
    await _login(user)
    await user.open("/")
    await user.should_see(t("pages_empty"))
    await user.should_see(t("no_requests"))

    await create_page(
        title="Fresh Dash Page", slug="fresh-dash-page",
        content="shows up as a dashboard page card",
        author_id=None, published=True, actor=SYSTEM_ACTOR
    )

    user.find(t("people"), kind=ui.tab).click()
    user.find(t("dashboard"), kind=ui.tab).click()
    await user.should_see("Fresh Dash Page")


async def test_dashboard_refreshes_when_actionable_count_changes(user: User) -> None:
    """A colleague advances a workflow while the user is parked on the
    dashboard: the badge poll notices the new actionable count and must
    also refresh the dashboard content, not just the tab label."""
    import uuid as _uuid

    from not_dot_net.backend.roles import RoleDefinition, roles_config
    from not_dot_net.backend.workflow_service import create_request, submit_step

    roles = await roles_config.get()
    roles.roles["director"] = RoleDefinition(
        label="Director", permissions=["create_workflows", "approve_workflows"],
    )
    roles.roles["staff"] = RoleDefinition(
        label="Staff", permissions=["create_workflows"],
    )
    await roles_config.set(roles)

    await _login(user, email="director-badge@not-dot-net.dev", role="director")
    await user.open("/")
    await user.should_see(t("no_pending"))

    async with session_scope() as session:
        staff = DbUser(
            id=_uuid.uuid4(), email="staff-badge@not-dot-net.dev",
            hashed_password="x", role="staff",
        )
        session.add(staff)
        await session.commit()
        await session.refresh(staff)

    req = await create_request(
        workflow_type="vpn_access", created_by=staff.id,
        data={"target_name": "Badge Target Person", "target_email": "bt@test.com"}, actor=SYSTEM_ACTOR
    )
    await submit_step(req.id, staff.id, "submit", data={}, actor_user=staff)

    with user.client:
        badge_timer = next(
            el for el in ElementFilter(kind=ui.timer) if el.interval == 60
        )
        await badge_timer.callback()

    await user.should_see("Badge Target Person")
