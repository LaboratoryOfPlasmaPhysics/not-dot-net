"""Admin page: AD Accounts settings + UID allocations table + Lock-from-AD button."""
from __future__ import annotations

from nicegui import ui

from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.errors import notify_error
from not_dot_net.frontend.ad_credentials import prompt_ad_credentials
from not_dot_net.backend.permissions import check_permission, MANAGE_SETTINGS


async def render(current_user) -> None:
    try:
        await check_permission(current_user, MANAGE_SETTINGS)
    except PermissionError:
        ui.notify(t("permission_denied"), type="negative")
        return

    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.uid_allocator import list_allocations, seed_from_ad
    from not_dot_net.frontend.admin_settings import _render_form

    ui.label(t("ad_accounts")).classes("text-h5 mb-3")

    cfg = await ad_account_config.get()
    await _render_form("ad_account", ad_account_config, cfg, current_user)

    ui.separator().classes("my-4")
    ui.label(t("lock_existing_ad_uids_intro")).classes("text-sm text-grey")

    table_container = ui.column().classes("w-full")

    async def _refresh_table():
        table_container.clear()
        rows = await list_allocations(limit=200)
        with table_container:
            if not rows:
                ui.label(t("uid_allocations_empty")).classes("text-grey")
                return
            ui.table(
                columns=[
                    {"name": "uid", "label": "UID", "field": "uid", "align": "left"},
                    {"name": "sam", "label": t("samaccountname"), "field": "sam_account", "align": "left"},
                    {"name": "source", "label": t("source"), "field": "source", "align": "left"},
                    {"name": "acquired_at", "label": t("acquired_at"), "field": "acquired_at", "align": "left"},
                ],
                rows=[
                    {
                        "uid": r.uid,
                        "sam_account": r.sam_account or "",
                        "source": r.source,
                        "acquired_at": r.acquired_at.isoformat(),
                    }
                    for r in rows
                ],
            ).props("dense flat bordered").classes("w-full")

    async def _on_lock():
        async def _on_bind(bind_user: str, bind_pw: str) -> None:
            try:
                result = await seed_from_ad(bind_user, bind_pw)
            except Exception as e:
                notify_error(e)
                return
            ui.notify(
                t("lock_existing_ad_uids_result", seeded=result.seeded, skipped=result.skipped),
                type="positive",
            )
            await _refresh_table()

        await prompt_ad_credentials(current_user, _on_bind)

    ui.button(t("lock_existing_ad_uids"), icon="lock", on_click=_on_lock).props("color=primary")

    ui.separator().classes("my-4")
    ui.label(t("recent_uid_allocations")).classes("text-h6")

    await _refresh_table()
