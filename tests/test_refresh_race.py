"""Overlapping refreshes must not draw a page twice.

Found in the 2026-11-12 ergonomics review: opening the app straight onto
Bookings showed every resource twice. The page's own first load and the
shell's tab refresh overlapped; each emptied the container, awaited the
database, then drew into it. Pages had the same shape.
"""

import asyncio

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import User, session_scope
from not_dot_net.frontend import bookings, pages


async def _viewer() -> User:
    async with session_scope() as session:
        viewer = User(email="race@test.com", hashed_password="x", is_active=True)
        session.add(viewer)
        await session.commit()
        await session.refresh(viewer)
    return viewer


async def _refresh_twice_at_once(user: UiUser, render, path: str, heading: str) -> int:
    viewer = await _viewer()
    refreshers = []

    @ui.page(path)
    def page():
        refreshers.append(render(viewer))

    await user.open(path)
    await user.should_see(heading)
    with user.client:
        await asyncio.gather(refreshers[0](), refreshers[0]())
    return len(user.find(heading).elements)


async def test_bookings_render_once(user: UiUser):
    assert await _refresh_twice_at_once(user, bookings.render, "/_bookings_race", "Resources") == 1


async def test_pages_render_once(user: UiUser):
    assert await _refresh_twice_at_once(user, pages.render, "/_pages_race", "Pages") == 1
