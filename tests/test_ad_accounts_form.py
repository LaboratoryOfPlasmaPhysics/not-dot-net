"""I8: AD settings in words an admin understands, and the empty-OU trap flagged."""

import uuid

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import User, session_scope
from not_dot_net.frontend.admin_ad_account import render


async def _root():
    """Saved: the permission check reloads the user from the database."""
    root = User(id=uuid.uuid4(), email=f"root-i8-{uuid.uuid4().hex[:6]}@test.com", hashed_password="x",
                is_active=True, is_superuser=True)
    async with session_scope() as session:
        session.add(root)
        await session.commit()
        await session.refresh(root)
    return root


async def test_fields_have_titles_and_visible_help(user: UiUser):
    root = await _root()

    @ui.page("/_ad_form")
    async def _page():
        await render(root)

    await user.open("/_ad_form")
    await user.should_see("Distinguished names of OUs in which new users may be created.")
    ous = next(iter(user.find(marker="users_ous").elements))
    assert ous.props["label"] == "Users OUs"
    uid_min = next(iter(user.find(marker="uid_min").elements))
    assert uid_min.props["label"] == "Lowest UID"


async def test_empty_user_ou_is_flagged(user: UiUser):
    root = await _root()

    @ui.page("/_ad_form_warn")
    async def _page():
        await render(root)

    await user.open("/_ad_form_warn")
    await user.should_see("No user OU is set")
