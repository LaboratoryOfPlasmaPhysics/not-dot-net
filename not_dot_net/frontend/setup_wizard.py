"""First-run setup wizard — shown when no super-user exists (production only)."""

import re

from nicegui import ui
from sqlalchemy import select

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.mail import mail_config
from not_dot_net.backend.users import ensure_default_admin
from not_dot_net.config import org_config, OrgConfig
from not_dot_net.frontend.i18n import t


MIN_SETUP_PASSWORD_LEN = 12

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_setup_credentials(email: str, password: str) -> str | None:
    """i18n key for what is wrong with the first super-user's credentials.

    This account cannot be recovered from inside the app — there is no other
    admin to reset it — so a typo in the address or a three-character password
    is worth catching before it becomes the only way in.
    """
    if not email or not password:
        return "setup_email_password_required"
    if not _EMAIL_RE.match(email.strip()):
        return "setup_invalid_email"
    if len(password) < MIN_SETUP_PASSWORD_LEN:
        return "setup_password_too_short"
    return None


async def has_superuser() -> bool:
    async with session_scope() as session:
        result = await session.execute(
            select(User).where(User.is_superuser.is_(True)).limit(1)
        )
        return result.scalar_one_or_none() is not None


async def complete_setup(
    email: str,
    password: str,
    *,
    smtp_host: str = "",
    smtp_port: int = 587,
    from_address: str = "",
) -> bool:
    """Create the bootstrap super-user; refuse if one appeared since the page
    loaded (a stale /setup tab must not mint a second superuser).

    Given an SMTP host, also writes the mail config and turns dev_mode off.
    Without one the safe default stands: mail is logged, not sent — better a
    blackhole than a misconfigured instance mailing real people.
    """
    if await has_superuser():
        return False
    await ensure_default_admin(email, password)
    if smtp_host.strip():
        cfg = await mail_config.get()
        await mail_config.set(cfg.model_copy(update={
            "smtp_host": smtp_host.strip(),
            "smtp_port": smtp_port,
            "from_address": from_address.strip() or cfg.from_address,
            "dev_mode": False,
        }))
    return True


def setup():
    @ui.page("/setup")
    async def setup_page():
        if await has_superuser():
            ui.navigate.to("/login")
            return

        email = ui.input(t("setup_admin_email")).props("outlined")
        password = ui.input(t("setup_admin_password"), password=True, password_toggle_button=True).props("outlined")
        confirm = ui.input(
            t("setup_confirm_password"), password=True, password_toggle_button=True,
        ).props("outlined")
        app_name = ui.input(t("setup_app_name"), value="LPP Intranet").props("outlined")

        ui.label(t("setup_mail_heading")).classes("text-sm text-weight-medium mt-4")
        ui.label(t("setup_mail_hint")).classes("text-xs text-grey")
        smtp_host = ui.input(t("smtp_host")).props("outlined")
        smtp_port = ui.number(t("smtp_port"), value=587, format="%d").props("outlined")
        from_address = ui.input(t("from_address")).props("outlined")

        async def on_submit():
            problem = validate_setup_credentials(email.value or "", password.value or "")
            if problem:
                ui.notify(t(problem), color="negative")
                return
            if password.value != confirm.value:
                ui.notify(t("setup_passwords_differ"), color="negative")
                return
            if not await complete_setup(
                email.value, password.value,
                smtp_host=smtp_host.value or "",
                smtp_port=int(smtp_port.value or 587),
                from_address=from_address.value or "",
            ):
                ui.navigate.to("/login")
                return
            if app_name.value:
                cfg = await org_config.get()
                await org_config.set(cfg.model_copy(update={"app_name": app_name.value}))
            ui.navigate.to("/login")

        ui.button(t("setup_complete"), on_click=on_submit).props("color=primary")
