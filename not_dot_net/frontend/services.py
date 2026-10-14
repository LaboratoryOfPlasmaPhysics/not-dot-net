"""Services tab — the lab's tools, grouped by category, filterable by tag and text."""

from nicegui import ui

from not_dot_net.config import ServiceEntry, services_config
from not_dot_net.frontend.i18n import get_locale, t


def filter_services(entries: list[ServiceEntry], query: str, tags: set[str]) -> list[ServiceEntry]:
    needle = query.strip().lower()
    return [
        e for e in entries
        if tags <= set(e.tags) and needle in f"{e.name} {e.description}".lower()
    ]


def group_by_category(entries: list[ServiceEntry]) -> dict[str, list[ServiceEntry]]:
    groups: dict[str, list[ServiceEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.category, []).append(entry)
    return groups


def localize(entry: ServiceEntry, glossary: dict[str, str]) -> ServiceEntry:
    return entry.model_copy(update={
        field: glossary.get(getattr(entry, field), getattr(entry, field))
        for field in ("name", "description", "category")
    })


def _all_tags(entries: list[ServiceEntry]) -> list[str]:
    return sorted({tag for e in entries for tag in e.tags})


def _render_card(entry: ServiceEntry, glossary: dict[str, str]):
    with ui.card().classes("q-py-sm q-px-md"):
        with ui.row().classes("items-center no-wrap"):
            ui.icon(entry.icon or "apps", color="primary").classes("text-2xl")
            ui.label(entry.name).classes("text-subtitle1 font-bold")
        if entry.description:
            ui.label(entry.description).classes("text-sm text-grey-8")
        if entry.tags:
            with ui.row().classes("gap-1"):
                for tag in entry.tags:
                    ui.badge(glossary.get(tag, tag), color="grey-4", text_color="grey-9")
        with ui.row().classes("gap-2"):
            if entry.url:
                ui.button(t("services_open"), icon="open_in_new",
                          on_click=lambda u=entry.url: ui.navigate.to(u, new_tab=True)).props("flat dense")
            if entry.doc_url:
                ui.button(t("services_docs"), icon="menu_book",
                          on_click=lambda u=entry.doc_url: ui.navigate.to(u, new_tab=True)).props("flat dense")


def _render_groups(entries: list[ServiceEntry], glossary: dict[str, str]):
    if not entries:
        ui.label(t("services_empty")).classes("text-grey-7")
        return
    for category, items in group_by_category(entries).items():
        if category:
            ui.label(category).classes("text-h6 q-mt-md")
        with ui.element("div").classes("w-full grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"):
            for entry in items:
                _render_card(entry, glossary)


def render(user):
    """Render the services tab content. Returns the refresh coroutine."""
    locale = get_locale()
    state = {"entries": [], "glossary": {}, "query": "", "tags": set()}
    filters = ui.row().classes("w-full items-center gap-2")
    results = ui.column().classes("w-full")

    def redraw():
        results.clear()
        with results:
            _render_groups(filter_services(state["entries"], state["query"], state["tags"]), state["glossary"])

    def on_search(e):
        state["query"] = e.value or ""
        redraw()

    def toggle_tag(tag: str, chip):
        state["tags"] ^= {tag}
        chip.props(f"color={'primary' if tag in state['tags'] else 'grey-4'}")
        redraw()

    def draw_filters():
        filters.clear()
        with filters:
            ui.input(t("services_search"), value=state["query"], on_change=on_search) \
                .props("dense clearable").classes("w-64")
            for tag in _all_tags(state["entries"]):
                chip = ui.chip(state["glossary"].get(tag, tag), color="primary" if tag in state["tags"] else "grey-4") \
                    .props("clickable").mark(f"tag-{tag}")
                chip.on_click(lambda _, tag=tag, chip=chip: toggle_tag(tag, chip))

    async def refresh():
        cfg = await services_config.get()
        state["glossary"] = cfg.translations.get(locale, {})
        state["entries"] = [localize(e, state["glossary"]) for e in cfg.services]
        state["tags"] &= set(_all_tags(state["entries"]))
        draw_filters()
        redraw()

    ui.timer(0, refresh, once=True)
    return refresh
