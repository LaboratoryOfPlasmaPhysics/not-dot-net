"""Reusable AD admin credentials prompt dialog."""
from __future__ import annotations

import asyncio

from typing import Awaitable, Callable

from nicegui import ui

from not_dot_net.backend.auth.ldap import (
    LdapModifyError,
    _ldap_bind,
    get_ldap_connect,
    ldap_config,
    store_user_connection,
)
from not_dot_net.frontend.i18n import t


async def prompt_ad_credentials(
    current_user, on_bind: Callable[[str, str], Awaitable[None]]
) -> None:
    """Show a credentials dialog. On successful bind, call on_bind(username, password).

    The bound connection is cached via store_user_connection before calling on_bind.
    """
    dialog = ui.dialog()
    with dialog, ui.card():
        ui.label(t("confirm_password_to_save_ad"))
        username_input = ui.input(t("ad_admin_username")).props("outlined dense")
        password_input = ui.input(t("password"), password=True).props("outlined dense")
        error_label = ui.label("").classes("text-negative")

        async def submit():
            bind_user = username_input.value.strip()
            if not bind_user or not password_input.value:
                error_label.set_text(t("username_password_required"))
                return
            cfg = await ldap_config.get()
            try:
                conn = await asyncio.to_thread(
                    _ldap_bind, bind_user, password_input.value, cfg, get_ldap_connect()
                )
            except LdapModifyError as e:
                msg = str(e)
                error_label.set_text(
                    t("ad_bind_failed") if "bind" in msg.lower() else t("ad_write_failed", error=msg)
                )
                return
            store_user_connection(str(current_user.id), conn)
            dialog.close()
            await on_bind(bind_user, password_input.value)

        with ui.row():
            ui.button(t("submit"), on_click=submit).props("flat color=primary")
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

    dialog.open()
