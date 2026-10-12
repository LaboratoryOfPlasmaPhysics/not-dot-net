"""/setup needs a one-time token from the server log (review 2026-10-12 S12).

Before, anyone who reached /setup while no superuser existed (fresh deploy,
or every superuser demoted) could create one.
"""

import logging

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.frontend import setup_wizard
from not_dot_net.frontend.setup_wizard import complete_setup, has_superuser, setup_token


async def test_wrong_token_creates_no_superuser():
    assert await complete_setup("intruder@test.dev", "pw12345678", token="guess") is False
    assert await has_superuser() is False


async def test_right_token_creates_the_superuser():
    assert await complete_setup("admin@test.dev", "pw12345678", token=setup_token()) is True
    assert await has_superuser() is True


async def test_page_without_token_hides_the_form_and_logs_the_link(user: UiUser, caplog):
    setup_wizard.setup()
    with caplog.at_level(logging.WARNING, logger=setup_wizard.__name__):
        await user.open("/setup")
    await user.should_not_see("Complete Setup")
    assert f"/setup?token={setup_token()}" in caplog.text


async def test_page_with_token_shows_the_form(user: UiUser):
    setup_wizard.setup()
    await user.open(f"/setup?token={setup_token()}")
    await user.should_see("Complete Setup")
