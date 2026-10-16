"""Statistics: how many people per status, employer or team, per year or period."""

import asyncio
from datetime import date

from nicegui import ui

from not_dot_net.backend.headcount import HeadcountTable, headcount, tenures_csv, year_periods
from not_dot_net.backend.vocabularies import field_options
from not_dot_net.frontend.i18n import get_locale, t

VOCABULARY_BY_DIMENSION = {"status": "employment_statuses", "employer": "employers", "team": "teams"}
YEARS_SHOWN = 5


async def render(user):
    """Render the page. Returns the refresh coroutine."""
    this_year = date.today().year
    state = {"by": "status", "mode": "years", "first": this_year - YEARS_SHOWN + 1, "last": this_year,
             "from": date.today().isoformat(), "to": date.today().isoformat()}

    with ui.row().classes("items-center gap-4"):
        ui.toggle({d: t(d) for d in VOCABULARY_BY_DIMENSION}, value=state["by"],
                  on_change=lambda e: _set(state, "by", e.value, refresh)).mark("stats-by")
        ui.toggle({"years": t("stats_per_year"), "period": t("stats_period")}, value=state["mode"],
                  on_change=lambda e: _set(state, "mode", e.value, refresh)).mark("stats-mode")
    controls = ui.row().classes("items-center gap-4")
    ui.label(t("stats_counting_rule")).classes("text-sm text-grey-8")
    table_box = ui.column().classes("w-full")
    ui.button(t("stats_download_csv"), icon="download", on_click=lambda: _download(user)).props("outline")

    lock = asyncio.Lock()
    shown_mode = None

    async def refresh():
        nonlocal shown_mode
        async with lock:
            if shown_mode != state["mode"]:  # redrawn on every change, a date input loses focus
                shown_mode = state["mode"]
                controls.clear()
                with controls:
                    _period_inputs(state, refresh)
            table = await headcount(_periods(state), state["by"], actor=user)
            labels = (await field_options(VOCABULARY_BY_DIMENSION[state["by"]], get_locale())).options
            table_box.clear()
            with table_box:
                _table(table, labels, state["mode"])

    await refresh()
    return refresh


async def _set(state: dict, key: str, value, refresh) -> None:
    state[key] = value
    await refresh()


def _period_inputs(state: dict, refresh) -> None:
    if state["mode"] == "years":
        for key, label in (("first", "stats_first_year"), ("last", "stats_last_year")):
            ui.number(t(label), value=state[key], format="%d", min=1900, max=2200,
                      on_change=lambda e, k=key: _set(state, k, int(e.value), refresh) if e.value else None,
                      ).props("dense outlined").classes("w-32")
        return
    for key, label in (("from", "stats_from"), ("to", "stats_to")):
        ui.input(t(label), value=state[key],
                 on_change=lambda e, k=key: _set(state, k, e.value, refresh) if _is_date(e.value) else None,
                 ).props("type=date dense outlined stack-label").mark(f"stats-{key}")


def _is_date(value) -> bool:
    try:
        date.fromisoformat(value or "")
        return True
    except ValueError:
        return False


def _periods(state: dict) -> list[tuple[date, date]]:
    if state["mode"] == "years":
        first, last = sorted((state["first"], state["last"]))
        return year_periods(first, last)
    start, end = sorted((date.fromisoformat(state["from"]), date.fromisoformat(state["to"])))
    return [(start, end)]


def _column_name(period: tuple[date, date], mode: str) -> tuple[str, str]:
    """(key, header) of a period's column."""
    start, end = period
    if mode == "years":
        return str(start.year), str(start.year)
    return "period", start.isoformat() if start == end else f"{start.isoformat()} → {end.isoformat()}"


def _table(table: HeadcountTable, labels: dict[str, str], mode: str) -> None:
    columns = [_column_name(p, mode) for p in table.periods]
    rows = [
        {"group": labels.get(g, g) if g else t("stats_unknown"), **dict(zip((k for k, _ in columns), counts))}
        for g, counts in table.rows.items()
    ]
    rows.append({"group": t("stats_total"), **dict(zip((k for k, _ in columns), table.totals))})
    ui.table(
        columns=[{"name": "group", "label": "", "field": "group", "align": "left"}]
        + [{"name": k, "label": h, "field": k} for k, h in columns],
        rows=rows, row_key="group",
    ).props("dense flat bordered").classes("w-full")


async def _download(user) -> None:
    ui.download((await tenures_csv(actor=user)).encode(), f"lab-tenures-{date.today().isoformat()}.csv")
