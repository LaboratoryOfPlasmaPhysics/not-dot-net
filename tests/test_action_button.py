"""Buttons that run async work can't be fired twice by a double click.

Review 2026-10-12 U3: nothing was disabled during async work, so a double
click on New request → Submit created two requests. NiceGUI runs an async
handler's body as a later background task, so the button must be disabled
synchronously, before the second click event is processed.
"""

import asyncio

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.frontend.widgets import action_button


async def test_double_click_runs_handler_once(user: UiUser):
    calls = []
    release = asyncio.Event()

    async def slow_submit():
        calls.append(1)
        await release.wait()

    @ui.page("/action-button-double-click")
    def page():
        action_button("Submit", slow_submit).mark("submit")

    await user.open("/action-button-double-click")
    user.find(marker="submit").click()
    user.find(marker="submit").click()
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.sleep(0.05)
    assert calls == [1]


async def test_button_is_usable_again_after_handler_finishes(user: UiUser):
    calls = []

    async def submit():
        calls.append(1)

    buttons = []

    @ui.page("/action-button-reenabled")
    def page():
        buttons.append(action_button("Submit", submit).mark("submit"))

    await user.open("/action-button-reenabled")
    user.find(marker="submit").click()
    await asyncio.sleep(0.05)
    user.find(marker="submit").click()
    await asyncio.sleep(0.05)
    assert calls == [1, 1]
    assert buttons[0].enabled


async def test_group_disables_sibling_buttons(user: UiUser):
    """Approve then Reject in quick succession must not both run."""
    calls = []
    release = asyncio.Event()

    async def act(name):
        calls.append(name)
        await release.wait()

    @ui.page("/action-button-group")
    def page():
        group: list = []
        action_button("Approve", lambda: act("approve"), group=group).mark("approve")
        action_button("Reject", lambda: act("reject"), group=group).mark("reject")

    await user.open("/action-button-group")
    user.find(marker="approve").click()
    user.find(marker="reject").click()
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.sleep(0.05)
    assert calls == ["approve"]
