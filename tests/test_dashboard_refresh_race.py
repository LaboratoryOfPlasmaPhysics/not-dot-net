"""Overlapping dashboard refreshes must not draw the cards twice.

Found during the 2026-10-12 review live check: the initial timer refresh and
the shell's tab refresh overlapped. Each cleared the container, awaited the
database, then drew into it — so both results landed, cards duplicated.
"""

import asyncio

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import User, session_scope
from not_dot_net.frontend.dashboard import render


async def test_concurrent_refreshes_render_once(user: UiUser):
    async with session_scope() as session:
        viewer = User(email="dash-race@test.com", hashed_password="x", is_active=True)
        session.add(viewer)
        await session.commit()
        await session.refresh(viewer)

    refreshers = []

    @ui.page("/_dash_race")
    def page():
        refreshers.append(render(viewer))

    await user.open("/_dash_race")
    await user.should_see("Awaiting My Action")
    with user.client:
        await asyncio.gather(refreshers[0](), refreshers[0]())
    assert len(user.find("Awaiting My Action").elements) == 1
