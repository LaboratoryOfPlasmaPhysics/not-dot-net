"""Public page route — renders markdown pages inside the app UI."""

from nicegui import ui

from not_dot_net.backend.page_service import get_page
from not_dot_net.frontend.i18n import t


def setup():
    @ui.page("/pages/{slug}")
    async def page_view(slug: str):
        page = await get_page(slug)

        ui.colors(primary="#0F52AC")
        with ui.header().classes("row items-center px-4").style(
            "background-color: #0F52AC"
        ):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat color=white"
            )
            ui.label(t("app_name")).classes("text-h6 text-white text-weight-light")

        if page is None or not page.published:
            with ui.column().classes("absolute-center items-center"):
                ui.icon("error", size="xl", color="negative")
                ui.label(t("page_not_found")).classes("text-h6")
            return

        with ui.column().classes("w-full max-w-3xl mx-auto pa-6"):
            ui.label(page.title).classes("text-h4 text-weight-light mb-4").style(
                "color: #0F52AC"
            )
            ui.markdown(page.content).classes("w-full")
