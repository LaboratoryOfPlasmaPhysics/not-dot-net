"""Each setting is edited in one place.

Review 2026-10-12 U10: Settings listed "Roles" twice (the role editor and
the generic `roles` section) and showed "AD Accounts", which has its own tab.
"""

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import User, session_scope
from not_dot_net.frontend.admin_settings import render


async def test_settings_lists_each_section_once(user: UiUser):
    async with session_scope() as session:
        root = User(email="settings-root@test.com", hashed_password="x", is_active=True, is_superuser=True)
        session.add(root)
        await session.commit()
        await session.refresh(root)

    @ui.page("/_settings_sections")
    async def page():
        await render(root)

    await user.open("/_settings_sections")
    await user.should_see("Organization")  # the config-section loop has rendered
    labels = [e.props.get("label") for e in user.find(kind=ui.expansion).elements]
    assert labels.count("Roles") == 1
    assert "AD Accounts" not in labels
