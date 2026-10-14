"""Change your own AD password — reachable logged out, for an expired one."""

import asyncio
from typing import Optional

from fastapi import Depends
from nicegui import ui

from not_dot_net.backend.audit import log_audit
from not_dot_net.backend.auth.ad_password import PasswordChangeError, change_own_password
from not_dot_net.backend.auth.ldap import ldap_config
from not_dot_net.backend.db import User
from not_dot_net.backend.users import current_active_user_optional
from not_dot_net.frontend.i18n import apply_client_locale, t
from not_dot_net.frontend.widgets import action_button

# impacket's kpasswd sets no socket timeout; a firewalled port 464 would hang.
CHANGE_TIMEOUT_S = 30


def setup():
    @ui.page("/change-password")
    async def change_password_page(
        username: str = "",
        expired: bool = False,
        user: Optional[User] = Depends(current_active_user_optional),
    ):
        apply_client_locale()
        if user is not None and user.ldap_username:
            username = user.ldap_username
        ui.colors(primary="#0F52AC")
        with ui.column().classes("absolute-center items-center gap-4"):
            ui.label(t("change_password")).classes("text-h5 text-weight-light").style("color: #0F52AC")
            with ui.card().classes("w-80 gap-2"):
                _render_form(username, expired, user)


def _render_form(username: str, expired: bool, user: Optional[User]) -> None:
    if expired:
        ui.label(t("password_expired_explain")).classes("text-orange-10")
    username_input = ui.input(t("ad_username"), value=username).props("outlined dense").classes("w-full")
    if user is not None and user.ldap_username:
        username_input.props("readonly")
    current = _password_input(t("current_password"), "cp-current")
    new = _password_input(t("new_password"), "cp-new")
    confirm = _password_input(t("confirm_new_password"), "cp-confirm")
    ui.label(t("password_rules_hint")).classes("text-xs text-grey-8")
    error = ui.label("").classes("text-negative")

    async def submit():
        if new.value != confirm.value:
            error.set_text(t("passwords_do_not_match"))
            return
        if not (username_input.value and current.value and new.value):
            error.set_text(t("all_fields_required"))
            return
        error.set_text("")
        try:
            await _change(username_input.value.strip(), current.value, new.value, user)
        except PasswordChangeError as exc:
            error.set_text(t(f"password_change_{exc.reason}"))
            return
        if user is None:
            ui.navigate.to("/login?changed=1")
        else:
            ui.notify(t("password_changed"), color="positive")
            ui.navigate.to("/")

    button = action_button(t("change_password"), submit).props("color=primary").classes("w-full").mark("cp-submit")
    # Through a client-side click, so Enter honours the disabled state too.
    confirm.on("keydown.enter", lambda: button.run_method("click"))
    ui.link(t("back_to_login") if user is None else t("back_home"), "/login" if user is None else "/")


def _password_input(label: str, marker: str) -> ui.input:
    return (
        ui.input(label, password=True, password_toggle_button=True)
        .props("outlined dense").classes("w-full").mark(marker)
    )


async def _change(username: str, old: str, new: str, user: Optional[User]) -> None:
    cfg = await ldap_config.get()
    # ldap3 and impacket are blocking; NiceGUI serves every client from one event loop.
    try:
        await asyncio.wait_for(
            asyncio.to_thread(change_own_password, username, old, new, cfg), CHANGE_TIMEOUT_S
        )
    except TimeoutError as exc:
        raise PasswordChangeError("unavailable") from exc
    await log_audit(
        "auth", "password_change",
        actor_id=user.id if user else None,
        actor_email=user.email if user else username,
        detail=f"username={username}",
    )
