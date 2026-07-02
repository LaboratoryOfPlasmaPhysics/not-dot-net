"""First-run setup wizard — shown when no super-user exists (production only)."""

from nicegui import ui
from sqlalchemy import select

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.users import ensure_default_admin
from not_dot_net.config import org_config, OrgConfig
from not_dot_net.frontend.i18n import t


async def has_superuser() -> bool:
    async with session_scope() as session:
        result = await session.execute(
            select(User).where(User.is_superuser.is_(True)).limit(1)
        )
        return result.scalar_one_or_none() is not None


async def complete_setup(email: str, password: str) -> bool:
    """Create the bootstrap super-user; refuse if one appeared since the page
    loaded (a stale /setup tab must not mint a second superuser)."""
    if await has_superuser():
        return False
    await ensure_default_admin(email, password)
    return True


def setup():
    @ui.page("/setup")
    async def setup_page():
        if await has_superuser():
            ui.navigate.to("/login")
            return

        email = ui.input(t("setup_admin_email")).props("outlined")
        password = ui.input(t("setup_admin_password"), password=True, password_toggle_button=True).props("outlined")
        app_name = ui.input(t("setup_app_name"), value="LPP Intranet").props("outlined")

        async def on_submit():
            if not email.value or not password.value:
                ui.notify(t("setup_email_password_required"), color="negative")
                return
            if not await complete_setup(email.value, password.value):
                ui.navigate.to("/login")
                return
            if app_name.value:
                cfg = await org_config.get()
                await org_config.set(cfg.model_copy(update={"app_name": app_name.value}))
            ui.navigate.to("/login")

        ui.button(t("setup_complete"), on_click=on_submit).props("color=primary")
