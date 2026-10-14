"""The open tab survives a language switch (review 2026-10-12 U14).

The tab was remembered by its translated label. Switching to French reloads
the page, the saved English label matched no French tab, and the user landed
back on the dashboard.
"""

from nicegui import app, ui
from nicegui.testing import User as UiUser

from tests.test_directory import _create_user_and_token


async def test_open_tab_survives_switching_language(user: UiUser):
    await user.open("/login")
    token = await _create_user_and_token("tab-keeper@test.com", "Secret123!")
    user.http_client.cookies.set("fastapiusersauth", token)
    await user.open("/")

    user.find(ui.tabs).elements.pop().set_value("bookings")
    await user.should_see("Bookings")
    with user.client:
        app.storage.user["locale"] = "fr"

    await user.open("/")
    assert user.find(ui.tabs).elements.pop().value == "bookings"
    await user.should_see("Réservations")
