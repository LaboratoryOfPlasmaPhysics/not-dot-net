"""Pages tab — list custom pages, inline editor for authorized users."""

import re

from nicegui import ui

from not_dot_net.backend.db import User
from not_dot_net.backend.page_service import (
    MANAGE_PAGES,
    create_page,
    delete_page,
    list_pages,
    get_page,
    update_page,
)
from not_dot_net.backend.permissions import check_permission, has_permissions
from not_dot_net.frontend.i18n import t


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-")


def render(user: User):
    container = ui.column().classes("w-full")

    async def refresh():
        await _render_page_list(container, user)

    ui.timer(0, refresh, once=True)


async def _render_page_list(container, user: User):
    container.clear()
    can_manage = await has_permissions(user, MANAGE_PAGES)
    pages = await list_pages(published_only=not can_manage)

    with container:
        with ui.row().classes("items-center justify-between w-full mb-3"):
            ui.label(t("pages")).classes("text-h6")
            if can_manage:
                ui.button(
                    t("new_page"), icon="add",
                    on_click=lambda: _show_editor(container, user),
                ).props("flat color=primary")

        if not pages:
            ui.label(t("page_not_found")).classes("text-grey")
            return

        for page in pages:
            with ui.card().classes("w-full q-py-sm q-px-md mb-2"):
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.row().classes("items-center gap-2"):
                        ui.link(page.title, f"/pages/{page.slug}").classes(
                            "text-subtitle1 font-bold"
                        )
                        if not page.published:
                            ui.badge(t("page_draft"), color="orange").props("dense")
                    if can_manage:
                        with ui.row().classes("gap-1"):
                            ui.button(
                                icon="edit",
                                on_click=lambda p=page: _show_editor(container, user, p),
                            ).props("flat dense round color=primary size=sm")

                            async def do_delete(p=page):
                                try:
                                    await check_permission(user, MANAGE_PAGES)
                                except PermissionError:
                                    ui.notify(t("permission_denied"), color="negative")
                                    return
                                await delete_page(p.id)
                                ui.notify(t("page_deleted"), color="positive")
                                await _render_page_list(container, user)

                            ui.button(
                                icon="delete", on_click=do_delete,
                            ).props("flat dense round color=negative size=sm")


async def _show_editor(container, user: User, page=None):
    editing = page is not None

    with ui.dialog().props("maximized") as dialog, ui.card().classes("w-full h-full"):
        # Top bar: metadata + actions
        with ui.row().classes("items-center gap-3 w-full mb-2"):
            title_input = ui.input(
                t("page_title"), value=page.title if editing else "",
            ).props("outlined dense").classes("flex-grow")

            slug_input = ui.input(
                t("page_slug"), value=page.slug if editing else "",
            ).props("outlined dense").classes("w-48")

            order_input = ui.number(
                t("page_sort_order"), value=page.sort_order if editing else 0,
            ).props("outlined dense").classes("w-28")

            published_toggle = ui.switch(
                t("page_published"), value=page.published if editing else False,
            )

            ui.space()

            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            async def do_save():
                try:
                    await check_permission(user, MANAGE_PAGES)
                except PermissionError:
                    ui.notify(t("permission_denied"), color="negative")
                    return
                if not title_input.value.strip():
                    ui.notify(t("required_field"), color="negative")
                    return
                slug_val = slug_input.value.strip() or _slugify(title_input.value)
                try:
                    if editing:
                        await update_page(
                            page.id,
                            title=title_input.value.strip(),
                            slug=slug_val,
                            content=content_input.value,
                            sort_order=int(order_input.value or 0),
                            published=published_toggle.value,
                        )
                    else:
                        await create_page(
                            title=title_input.value.strip(),
                            slug=slug_val,
                            content=content_input.value,
                            author_id=user.id,
                            sort_order=int(order_input.value or 0),
                            published=published_toggle.value,
                        )
                except ValueError as e:
                    ui.notify(str(e), color="negative")
                    return
                ui.notify(t("page_saved"), color="positive")
                dialog.close()
                await _render_page_list(container, user)

            ui.button(t("save"), icon="save", on_click=do_save).props("color=primary")

        if not editing:
            title_input.on_value_change(
                lambda e: slug_input.set_value(_slugify(e.value))
            )

        # Side-by-side: CodeMirror editor + live preview
        with ui.splitter(value=50).classes("w-full flex-grow") as splitter:
            with splitter.before:
                content_input = ui.codemirror(
                    value=page.content if editing else "",
                    language="Markdown",
                    theme="githubLight",
                    line_wrapping=True,
                ).classes("w-full h-full")

            with splitter.after:
                preview = ui.markdown(
                    page.content if editing else "",
                ).classes("pa-4 w-full overflow-auto")

        content_input.on_value_change(lambda e: preview.set_content(e.value))

    dialog.open()
