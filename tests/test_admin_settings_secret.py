"""Secret settings never reach the browser (review 2026-10-12 S10).

The SMTP password input was only masked: its value still sat in the page,
readable with the toggle button or the dev tools.
"""

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.mail import MailConfig, mail_config
from not_dot_net.frontend.admin_settings import _render_form


async def _open_mail_form(user: UiUser, path: str):
    await mail_config.set(MailConfig(smtp_user="relay", smtp_password="hunter2"))
    async with session_scope() as session:
        root = User(email=f"secret-root{path}@test.com", hashed_password="x", is_active=True, is_superuser=True)
        session.add(root)
        await session.commit()
        await session.refresh(root)

    @ui.page(path)
    async def page():
        await _render_form("mail", mail_config, await mail_config.get(), root)

    await user.open(path)


def _password_input(user: UiUser) -> ui.input:
    return next(iter(user.find(kind=ui.input, marker="smtp_password").elements))


async def test_stored_secret_is_not_sent_and_survives_save(user: UiUser):
    await _open_mail_form(user, "/secret-blank")
    assert _password_input(user).value == ""

    user.find(marker="form-save").click()
    await user.should_see("Settings saved")
    assert (await mail_config.get()).smtp_password == "hunter2"


async def test_typed_secret_replaces_stored_one(user: UiUser):
    await _open_mail_form(user, "/secret-typed")
    _password_input(user).value = "new-pass"

    user.find(marker="form-save").click()
    await user.should_see("Settings saved")
    assert (await mail_config.get()).smtp_password == "new-pass"
