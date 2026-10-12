"""One-shot and destructive UI actions must not be lost or fired by accident.

Review 2026-10-12 U1: the one-time AD password dialog closed on Esc/backdrop,
losing the password for good. U2: a tenure was deleted on a single click.
"""

import uuid
from datetime import date

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import SYSTEM_ACTOR
from not_dot_net.backend.tenure_service import add_tenure, list_tenures
from not_dot_net.frontend.directory import _render_tenure_history
from not_dot_net.frontend.workflow_detail import _show_temp_password_dialog


async def test_temp_password_dialog_cannot_be_dismissed_by_escape(user: UiUser):
    dialogs = []

    @ui.page("/temp-password-dialog-test")
    def page():
        dialogs.append(_show_temp_password_dialog("S3cret-pw"))

    await user.open("/temp-password-dialog-test")
    await user.should_see("S3cret-pw")
    assert "persistent" in dialogs[0].props


async def test_tenure_delete_asks_for_confirmation(user: UiUser):
    async with session_scope() as session:
        person = User(id=uuid.uuid4(), email="tenure-person@test.com", hashed_password="x", role="")
        session.add(person)
        await session.commit()
        await session.refresh(person)
    await add_tenure(person.id, "postdoc", "CNRS", date(2025, 1, 1), actor=SYSTEM_ACTOR)

    @ui.page("/tenure-delete-confirm-test")
    async def page():
        await _render_tenure_history(ui.column(), person, SYSTEM_ACTOR, is_admin=True)

    await user.open("/tenure-delete-confirm-test")
    user.find(marker="delete-tenure").click()
    await user.should_see("Delete this employment period?")
    assert len(await list_tenures(person.id)) == 1
