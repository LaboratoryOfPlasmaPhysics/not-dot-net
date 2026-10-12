"""A directory search that matches nobody says so (review 2026-10-12 U11).

The grid just went blank, which reads as "still loading" or "broken".
"""

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import User, session_scope
from not_dot_net.frontend.directory import render


async def test_search_with_no_match_shows_a_message(user: UiUser):
    async with session_scope() as session:
        viewer = User(email="searcher@test.com", hashed_password="x", is_active=True, full_name="Searcher")
        session.add(viewer)
        await session.commit()
        await session.refresh(viewer)

    @ui.page("/_dir_search")
    def page():
        render(viewer)

    await user.open("/_dir_search")
    await user.should_see("Searcher")
    await user.should_not_see("No one matches")

    user.find(ui.input).type("zzz-nobody")
    await user.should_see("No one matches")

    user.find(ui.input).clear().type("search")
    await user.should_not_see("No one matches")
