"""The Statistics page: headcounts per year or period, grouped as asked."""

import asyncio
import uuid
from datetime import date

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import SYSTEM_ACTOR
from not_dot_net.backend.tenure_service import add_tenure
from not_dot_net.frontend.statistics import render

THIS_YEAR = date.today().year


async def _admin_and_a_phd() -> User:
    async with session_scope() as session:
        admin = User(id=uuid.uuid4(), email="stats-admin@test.com", hashed_password="x",
                     is_active=True, is_superuser=True)
        phd = User(id=uuid.uuid4(), email="stats-phd@test.com", hashed_password="x")
        session.add_all([admin, phd])
        await session.commit()
        await session.refresh(admin)
    await add_tenure(phd.id, "PhD", "CNRS", date(THIS_YEAR - 1, 10, 1), team="Theory", actor=SYSTEM_ACTOR)
    return admin


def _table(user: UiUser) -> ui.table:
    return next(iter(user.find(kind=ui.table).elements))


def _cells(table: ui.table, group: str) -> dict:
    return next(r for r in table.rows if r["group"] == group)


async def _wait_for_count(user: UiUser, group: str, column: str, expected: int) -> None:
    """The table redraws after an awaited query, not at once."""
    for _ in range(100):
        rows = [r for t in user.find(kind=ui.table).elements for r in t.rows if r["group"] == group]
        if rows and rows[0].get(column) == expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{group!r}/{column} never showed {expected}: {rows}")


async def test_default_view_counts_statuses_per_year(user: UiUser):
    admin = await _admin_and_a_phd()

    @ui.page("/_stats")
    async def _page():
        await render(admin)

    await user.open("/_stats")
    await user.should_see(kind=ui.table)
    table = _table(user)
    phd = _cells(table, "PhD")
    assert phd[str(THIS_YEAR - 2)] == 0
    assert phd[str(THIS_YEAR - 1)] == 1 and phd[str(THIS_YEAR)] == 1
    assert _cells(table, "Total people")[str(THIS_YEAR)] == 1


async def test_grouping_by_team(user: UiUser):
    admin = await _admin_and_a_phd()

    @ui.page("/_stats_team")
    async def _page():
        await render(admin)

    await user.open("/_stats_team")
    await user.should_see(kind=ui.table)
    user.find(marker="stats-by").elements.pop().set_value("team")
    await _wait_for_count(user, "Theory", str(THIS_YEAR), 1)


async def test_a_single_date(user: UiUser):
    admin = await _admin_and_a_phd()

    @ui.page("/_stats_day")
    async def _page():
        await render(admin)

    await user.open("/_stats_day")
    await user.should_see(kind=ui.table)
    user.find(marker="stats-mode").elements.pop().set_value("period")
    await user.should_see(marker="stats-from")
    user.find(marker="stats-from").elements.pop().set_value(f"{THIS_YEAR - 1}-01-01")
    user.find(marker="stats-to").elements.pop().set_value(f"{THIS_YEAR - 1}-01-01")
    await _wait_for_count(user, "Total people", "period", 0)
