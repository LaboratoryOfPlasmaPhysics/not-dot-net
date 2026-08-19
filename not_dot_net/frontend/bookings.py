"""Bookings tab — resource list, booking calendar, admin management."""

import uuid
from datetime import date, timedelta

from nicegui import ui
from sqlalchemy import func, select

from not_dot_net.backend.booking_service import (
    BookingConflictError,
    BookingValidationError,
    available_transitions,
    cancel_booking,
    create_booking,
    create_resource,
    delete_resource,
    list_bookings_for_resource,
    list_bookings_for_user,
    list_resources,
    migrate_booking,
    restore_resource,
    set_resource_status,
    update_resource,
)
from not_dot_net.config import bookings_config
from not_dot_net.backend.db import User, resolve_user_names, session_scope
from not_dot_net.backend.permissions import has_permissions
from not_dot_net.backend.vocabularies import resolve_terms
from not_dot_net.frontend.i18n import t
from not_dot_net.frontend.widgets import confirm_dialog

RESOURCE_TYPES = ["desktop", "laptop", "office"]


def _minimum_booking_start(today: date | None = None, minimum_lead_days: int = 7) -> date:
    return (today or date.today()) + timedelta(days=minimum_lead_days)


def _default_booking_range(today: date | None = None, minimum_lead_days: int = 7) -> dict[str, str]:
    start = _minimum_booking_start(today, minimum_lead_days)
    return {"from": str(start), "to": str(start + timedelta(days=7))}


def _normalize_booking_range(
    value,
    today: date | None = None,
    minimum_lead_days: int = 7,
) -> dict[str, str]:
    default_range = _default_booking_range(today, minimum_lead_days)
    if not isinstance(value, dict):
        return default_range
    try:
        start = date.fromisoformat(value["from"])
        end = date.fromisoformat(value["to"])
    except (KeyError, TypeError, ValueError):
        return default_range

    min_start = _minimum_booking_start(today, minimum_lead_days)
    if start >= min_start:
        return {"from": str(start), "to": str(end)}

    duration = max((end - start).days, 1)
    return {"from": str(min_start), "to": str(min_start + timedelta(days=duration))}


def _qdate_option_date(value: date) -> str:
    return value.isoformat().replace("-", "/")


def _truncate_booking_owner(name: str, max_chars: int = 24) -> str:
    return name if len(name) <= max_chars else f"{name[:max_chars]}..."


def _format_booking_period(start: date, end_exclusive: date) -> str:
    """end_date is the exclusive hand-back day — show the inclusive range
    the user actually picked."""
    return f"{start} → {end_exclusive - timedelta(days=1)}"


def render(user: User):
    container = ui.column().classes("w-full")

    async def refresh():
        await _render_bookings(container, user)

    ui.timer(0, refresh, once=True)
    return refresh



async def compute_availability(
    resources, *, range_start: date, range_end: date, setup_buffer_days: int,
) -> tuple[dict[uuid.UUID, bool], dict[uuid.UUID, object]]:
    """Map each resource to free/busy over the window, plus the blocking booking.

    One batched query for the whole list — this runs on every render, date-range
    change, filter change and post-booking re-render.
    """
    from not_dot_net.backend.booking_service import list_bookings_for_resources

    buffer = timedelta(days=setup_buffer_days)
    window_start, window_end = range_start - buffer, range_end + buffer

    by_resource = await list_bookings_for_resources(
        [r.id for r in resources], from_date=window_start, to_date=window_end,
    )

    availability: dict[uuid.UUID, bool] = {}
    conflicts: dict[uuid.UUID, object] = {}
    for res in resources:
        blocking = next((
            b for b in by_resource.get(res.id, [])
            if b.start_date < window_end and b.end_date > window_start
        ), None)
        if blocking is not None:
            conflicts[res.id] = blocking
        availability[res.id] = blocking is None
    return availability, conflicts


async def _render_bookings(container, user: User, filter_range=None):
    container.clear()
    is_admin = await has_permissions(user, "manage_bookings")
    all_resources = await list_resources(active_only=not is_admin)
    resources = _exclude_offices(all_resources)
    logged_in = user.is_active
    my_bookings = await list_bookings_for_user(user.id) if logged_in else []
    booking_cfg = await bookings_config.get()

    with container:
        # --- My Bookings ---
        if my_bookings:
            ui.label(t("my_bookings")).classes("text-h6 mb-2")
            with ui.element("div").classes(
                "w-full grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mb-4"
            ):
                for bk in my_bookings:
                    res = _get_resource_for_booking(bk.resource_id, all_resources)
                    res_name = res.name if res else "?"
                    with ui.card().classes("q-py-sm q-px-md"):
                        with ui.row().classes("items-center justify-between w-full"):
                            with ui.column().classes("gap-0"):
                                ui.label(res_name).classes("font-bold")
                                ui.label(
                                    _format_booking_period(bk.start_date, bk.end_date)
                                ).classes("text-sm text-grey-8")
                                if bk.os_choice:
                                    ui.label(bk.os_choice).classes("text-xs text-grey")
                                if bk.note:
                                    ui.label(bk.note).classes("text-xs text-grey")

                            async def do_cancel(b=bk):
                                try:
                                    await cancel_booking(b.id, actor=user)
                                except Exception as e:
                                    ui.notify(str(e), color="negative")
                                    return
                                ui.notify(t("booking_cancelled"), color="positive")
                                await _render_bookings(container, user)

                            cancel_dlg = confirm_dialog(
                                t("confirm_cancel_booking"), do_cancel,
                                confirm_label=t("cancel_booking"), confirm_icon="event_busy",
                            )
                            ui.button(
                                icon="close", on_click=cancel_dlg.open,
                            ).props("flat dense round color=negative size=sm").tooltip(
                                t("cancel_booking")
                            )

            ui.separator().classes("mb-4")

        # --- Global date range filter ---
        today = date.today()
        default_range = _normalize_booking_range(
            filter_range,
            today,
            booking_cfg.minimum_lead_days,
        )
        state = {"range": default_range}

        def _range_label(r):
            return f"{r['from']} → {r['to']}" if isinstance(r, dict) else ""

        sites = [term.code for term in await resolve_terms("sites")]

        with ui.row().classes("items-center gap-2 mb-3"):
            ui.icon("date_range", size="sm").classes("text-primary")
            with ui.element("div"):
                range_display = ui.input(
                    t("filter"), value=_range_label(default_range),
                ).props("outlined dense readonly").classes("min-w-[250px]")
                with range_display.add_slot("append"):
                    ui.icon("event").classes("cursor-pointer")
                with ui.menu() as menu:
                    min_start = _minimum_booking_start(today, booking_cfg.minimum_lead_days)
                    min_start_option = _qdate_option_date(min_start)
                    date_picker = ui.date(default_range).props(
                        f"range :options=\"date => date >= '{min_start_option}'\""
                    )

            all_sites = [t("all_locations")] + sites
            site_select = ui.select(
                options=all_sites, value=all_sites[0],
                label=t("resource_location"),
            ).props("outlined dense").classes("min-w-[150px]")

            equipment_types = [rt for rt in RESOURCE_TYPES if rt != "office"]
            all_types = [t("all_types")] + equipment_types
            type_select = ui.select(
                options=all_types, value=all_types[0],
                label=t("resource_type"),
            ).props("outlined dense").classes("min-w-[150px]")

        resource_area = ui.column().classes("w-full")

        async def apply_filter():
            val = date_picker.value
            if not val or not isinstance(val, dict):
                return
            normalized = _normalize_booking_range(val, minimum_lead_days=booking_cfg.minimum_lead_days)
            if normalized != val:
                date_picker.value = normalized
                date_picker.update()
            state["range"] = normalized
            range_display.value = _range_label(normalized)
            menu.close()
            await _render_resource_list(
                container, resource_area, resources, user, is_admin, normalized,
                site_filter=site_select.value if site_select.value in sites else None,
                type_filter=type_select.value if type_select.value in equipment_types else None,
                setup_buffer_days=booking_cfg.resource_setup_buffer_days,
            )

        date_picker.on_value_change(lambda _: apply_filter())
        site_select.on_value_change(lambda _: apply_filter())
        type_select.on_value_change(lambda _: apply_filter())

        # --- Resources header ---
        with ui.row().classes("items-center justify-between w-full mb-2"):
            ui.label(t("resources")).classes("text-h6")
            if is_admin:
                with ui.row().classes("gap-2"):
                    ui.button(
                        t("add_resource"), icon="add",
                        on_click=lambda: _show_resource_dialog(container, user),
                    ).props("flat color=primary")
                    ui.button(
                        t("manage_software"), icon="settings",
                        on_click=lambda: _show_software_dialog(container, user),
                    ).props("flat color=primary")

        if not resources:
            ui.label(t("no_bookings")).classes("text-grey")
            return

        # Initial render with default range
        await _render_resource_list(
            container, resource_area, resources, user, is_admin, default_range,
            setup_buffer_days=booking_cfg.resource_setup_buffer_days,
        )


async def _render_resource_list(outer_container, area, resources, user, is_admin, date_range,
                                site_filter=None, type_filter=None, setup_buffer_days: int | None = None):
    """Render resource cards filtered by availability, site, and type."""
    area.clear()
    try:
        range_start = date.fromisoformat(date_range["from"])
        range_end = date.fromisoformat(date_range["to"]) + timedelta(days=1)
    except (ValueError, KeyError):
        return

    # Apply site and type filters
    filtered = resources
    if site_filter:
        filtered = [r for r in filtered if r.location == site_filter]
    if type_filter:
        filtered = [r for r in filtered if r.resource_type == type_filter]

    # Build availability map
    setup_buffer_days = (
        setup_buffer_days
        if setup_buffer_days is not None
        else (await bookings_config.get()).resource_setup_buffer_days
    )
    availability, conflict_bookings = await compute_availability(
        filtered, range_start=range_start, range_end=range_end,
        setup_buffer_days=setup_buffer_days,
    )

    owner_labels = {}
    if conflict_bookings:
        names = await resolve_user_names({b.user_id for b in conflict_bookings.values()})
        for resource_id, booking in conflict_bookings.items():
            owner_labels[resource_id] = _truncate_booking_owner(
                names.get(booking.user_id, "?")
            )

    sites = [term.code for term in await resolve_terms("sites")]
    state = {"expanded_id": None}

    with area:
        if not filtered:
            ui.label(t("no_bookings")).classes("text-grey")
            return

        # Group by site
        by_site: dict[str, list] = {s: [] for s in sites}
        by_site[""] = []
        for res in filtered:
            key = res.location if res.location in by_site else ""
            by_site[key].append(res)

        for site, site_resources in by_site.items():
            if not site_resources:
                continue
            if site:
                ui.label(site).classes("text-subtitle1 font-bold mt-3 mb-1")

            # Available first, then booked
            site_resources.sort(key=lambda r: (not availability.get(r.id, True)))

            with ui.element("div").classes(
                "w-full grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3"
            ):
                for res in site_resources:
                    await _resource_card(
                        outer_container, res, user, is_admin, state,
                        is_available=availability.get(res.id, True),
                        booked_by=owner_labels.get(res.id),
                        book_range=date_range,
                    )


_STATUS_COLOR = {
    "available": "positive",
    "booked": "orange",
    "ready": "primary",
    "in_use": "blue",
    "returned": "purple",
    "out_of_service": "negative",
}


def _status_color(status: str) -> str:
    return _STATUS_COLOR.get(status, "grey")


_RESOURCE_ICON = {"desktop": "desktop_windows", "laptop": "laptop", "office": "meeting_room"}


def _resource_icon(resource_type: str) -> str:
    return _RESOURCE_ICON.get(resource_type, "devices")


def _office_fields_visible(resource_type: str) -> bool:
    return resource_type == "office"


def _exclude_offices(resources: list) -> list:
    return [r for r in resources if r.resource_type != "office"]


def _get_resource_for_booking(resource_id, resources):
    for r in resources:
        if r.id == resource_id:
            return r
    return None


async def _load_active_users() -> list[User]:
    async with session_scope() as session:
        result = await session.execute(
            select(User).where(User.is_active == True).order_by(  # noqa: E712
                func.lower(func.coalesce(User.full_name, User.email))
            )
        )
        return list(result.scalars().all())


async def _resource_card(outer_container, res, user, is_admin, state,
                         is_available=True, booked_by=None, book_range=None):
    with ui.card().classes("cursor-pointer q-py-sm q-px-md") as card:
        with ui.row().classes("items-center justify-between w-full"):
            with ui.column().classes("gap-0"):
                with ui.row().classes("items-center gap-2"):
                    icon = _resource_icon(res.resource_type)
                    ui.icon(icon, size="sm").classes("text-grey-7")
                    ui.label(res.name).classes("font-bold")
                ui.label(t(res.resource_type)).classes("text-xs text-grey")
                if res.specs:
                    specs = res.specs
                    parts = []
                    if specs.get("cpu"):
                        parts.append(specs["cpu"])
                    if specs.get("ram"):
                        parts.append(specs["ram"])
                    if specs.get("gpu") and specs["gpu"] != "—":
                        parts.append(specs["gpu"])
                    if parts:
                        ui.label(" · ".join(parts)).classes("text-xs text-grey-6")
            ui.badge(
                t("available") if is_available else f"{t('booked_by')} {booked_by or '?'}",
                color="positive" if is_available else "orange",
            )

        with ui.row().classes("items-center gap-1 mt-1"):
            ui.badge(t(f"status_{res.status}"), color=_status_color(res.status))
            if not res.active:
                ui.badge(t("retired"), color="grey")

        detail = ui.column().classes("w-full mt-2")
        detail.set_visibility(False)
        detail.on("click.stop", js_handler="() => {}")

        async def toggle(dc=detail, r=res, st=state):
            if st["expanded_id"] == r.id:
                dc.set_visibility(False)
                st["expanded_id"] = None
                return
            st["expanded_id"] = r.id
            dc.set_visibility(True)
            dc.clear()
            with dc:
                ui.separator()
                await _render_resource_detail(
                    outer_container, r, user, is_admin, book_range=book_range,
                )

        card.on("click", toggle)


async def _render_resource_detail(outer_container, res, user, is_admin, book_range=None):
    if res.description:
        ui.label(res.description).classes("text-sm text-grey-8 mb-2")

    # Specs
    if res.specs:
        with ui.row().classes("gap-4 text-caption mb-2"):
            for key in ("cpu", "ram", "hdd", "gpu"):
                val = res.specs.get(key)
                if val and val != "—":
                    ui.label(f"{t(key)}: {val}")

    # Upcoming bookings
    today = date.today()
    bookings = await list_bookings_for_resource(
        res.id, from_date=today, to_date=today + timedelta(days=90),
    )

    if bookings:
        owner_names = await resolve_user_names([bk.user_id for bk in bookings])
        ui.label(t("bookings")).classes("text-subtitle2 mt-2 mb-1")
        for bk in bookings:
            owner_name = owner_names.get(bk.user_id, "?")
            is_own = bk.user_id == user.id
            with ui.row().classes("items-center gap-2 w-full flex-wrap"):
                ui.label(
                    _format_booking_period(bk.start_date, bk.end_date)
                ).classes("text-sm")
                ui.label(owner_name).classes("text-sm text-grey")
                if bk.os_choice:
                    ui.badge(bk.os_choice, color="blue-grey").props("dense")
                if bk.software_tags:
                    for sw in bk.software_tags:
                        ui.badge(sw, color="grey").props("dense outline")
                if is_own or is_admin:
                    async def do_cancel(b=bk):
                        try:
                            await cancel_booking(b.id, actor=user)
                        except Exception as e:
                            ui.notify(str(e), color="negative")
                            return
                        ui.notify(t("booking_cancelled"), color="positive")
                        await _render_bookings(outer_container, user)

                    cancel_dlg = confirm_dialog(
                        t("confirm_cancel_booking"), do_cancel,
                        confirm_label=t("cancel_booking"), confirm_icon="event_busy",
                    )
                    ui.button(icon="close", on_click=cancel_dlg.open).props(
                        "flat dense round size=xs color=negative"
                    ).tooltip(t("cancel_booking"))

    # Book form — only for authenticated users
    if not user.is_active:
        return

    ui.label(t("book")).classes("text-subtitle2 mt-3 mb-1")
    bc = await bookings_config.get()
    default_range = _normalize_booking_range(
        book_range or _default_booking_range(today, bc.minimum_lead_days),
        today,
        bc.minimum_lead_days,
    )
    range_label = f"{default_range['from']} → {default_range['to']}"

    os_choices = bc.os_choices
    all_software = bc.software_tags

    ui.label(range_label).classes("text-sm text-grey-8")
    with ui.row().classes("items-center gap-2"):
        ui.label(t("os")).classes("text-sm")
        os_select = ui.toggle(os_choices, value=None).props("dense")

    chip_state = {"selected": set()}
    sw_container = ui.row().classes("flex-wrap gap-1")

    def _rebuild_chips(os_name):
        sw_container.clear()
        chip_state["selected"] = set()
        if not os_name:
            return
        tags = all_software.get(os_name, [])
        with sw_container:
            for tag in tags:
                chip = ui.chip(tag, color="grey-3", text_color="grey-8").props("dense")

                def toggle(_, t=tag, c=chip):
                    if t in chip_state["selected"]:
                        chip_state["selected"].discard(t)
                        c._props["color"] = "grey-3"
                        c._props["text-color"] = "grey-8"
                    else:
                        chip_state["selected"].add(t)
                        c._props["color"] = "primary"
                        c._props["text-color"] = "white"
                    c.update()

                chip.on_click(toggle)

    def on_os_change(e):
        _rebuild_chips(e.value)

    os_select.on_value_change(on_os_change)

    with ui.row().classes("items-center gap-2"):
        note_input = ui.input(t("note")).props("outlined dense")

        async def _submit_booking(selected_sw):
            try:
                s = date.fromisoformat(default_range["from"])
                e = date.fromisoformat(default_range["to"]) + timedelta(days=1)
            except (ValueError, KeyError):
                ui.notify("Invalid date range", color="negative")
                return
            try:
                await create_booking(
                    res.id, user.id, s, e,
                    note=note_input.value,
                    os_choice=os_select.value,
                    software_tags=selected_sw or None,
                    actor=user,
                )
            except (BookingConflictError, BookingValidationError) as err:
                ui.notify(str(err), color="negative")
                return
            ui.notify(t("booking_created"), color="positive")
            await _render_bookings(outer_container, user)

        async def do_book():
            if not os_select.value:
                ui.notify(t("select_os"), color="warning")
                return
            selected_sw = list(chip_state["selected"])
            if not selected_sw:
                with ui.dialog() as confirm_dialog, ui.card():
                    ui.label(t("no_software_confirm"))

                    async def confirm():
                        confirm_dialog.close()
                        await _submit_booking(selected_sw)

                    with ui.row():
                        ui.button(t("cancel"), on_click=confirm_dialog.close).props("flat")
                        ui.button(t("book_anyway"), on_click=confirm).props("color=primary")
                confirm_dialog.open()
                return
            await _submit_booking(selected_sw)

        ui.button(t("book"), on_click=do_book).props("color=primary")

    # Admin controls
    if is_admin:
        ui.separator().classes("mt-3")

        if res.resource_type != "office" and res.active:
            with ui.row().classes("items-center gap-2 mt-2"):
                ui.label(t("status") + ":").classes("text-sm")
                ui.badge(t(f"status_{res.status}"), color=_status_color(res.status))
            with ui.row().classes("gap-2 mt-1 flex-wrap"):
                for nxt in available_transitions(res.status):
                    async def do_transition(target=nxt):
                        try:
                            await set_resource_status(res.id, target, actor=user)
                        except Exception as e:
                            ui.notify(str(e), color="negative")
                            return
                        ui.notify(t("status_updated"), color="positive")
                        await _render_bookings(outer_container, user)

                    ui.button(t(f"mark_{nxt}"), on_click=do_transition).props(
                        "flat dense color=primary"
                    )

        ui.separator().classes("mt-3")
        with ui.row().classes("gap-2 mt-2"):
            ui.button(
                t("edit_resource"), icon="edit",
                on_click=lambda: _show_resource_dialog(
                    outer_container, user, resource=res,
                ),
            ).props("flat dense color=primary")

            if res.active:
                async def do_retire():
                    with ui.dialog() as dlg, ui.card():
                        ui.label(t("retire_confirm"))

                        async def confirm():
                            dlg.close()
                            try:
                                await update_resource(res.id, active=False, actor=user)
                            except Exception as e:
                                ui.notify(str(e), color="negative")
                                return
                            ui.notify(t("resource_retired"), color="positive")
                            await _render_bookings(outer_container, user)

                        with ui.row():
                            ui.button(t("cancel"), on_click=dlg.close).props("flat")
                            ui.button(t("retire"), on_click=confirm).props("color=warning")
                    dlg.open()

                ui.button(t("retire"), icon="archive", on_click=do_retire).props(
                    "flat dense color=warning"
                )
            else:
                async def do_restore():
                    try:
                        await restore_resource(res.id, actor=user)
                    except Exception as e:
                        ui.notify(str(e), color="negative")
                        return
                    ui.notify(t("resource_restored"), color="positive")
                    await _render_bookings(outer_container, user)

                async def do_delete():
                    upcoming = await list_bookings_for_resource(res.id, from_date=date.today())
                    if upcoming:
                        await _show_migration_dialog(outer_container, user, res, upcoming)
                        return
                    with ui.dialog() as dlg, ui.card():
                        ui.label(t("delete_confirm"))

                        async def confirm():
                            dlg.close()
                            try:
                                await delete_resource(res.id, actor=user)
                            except Exception as e:
                                ui.notify(str(e), color="negative")
                                return
                            ui.notify(t("resource_deleted"), color="positive")
                            await _render_bookings(outer_container, user)

                        with ui.row():
                            ui.button(t("cancel"), on_click=dlg.close).props("flat")
                            ui.button(t("delete"), on_click=confirm).props("color=negative")
                    dlg.open()

                ui.button(t("restore"), icon="unarchive", on_click=do_restore).props(
                    "flat dense color=positive"
                )
                ui.button(t("delete"), icon="delete", on_click=do_delete).props(
                    "flat dense color=negative"
                )


async def _show_migration_dialog(outer_container, user, res, upcoming):
    """Deleting a resource with upcoming bookings: one row per booking with a
    target-resource select; everything must be migrated before the delete runs."""
    others = [r for r in await list_resources(active_only=True) if r.id != res.id]
    if not others:
        ui.notify(t("migrate_no_target"), color="negative")
        return
    options = {str(r.id): (f"{r.name} ({r.location})" if r.location else r.name)
               for r in others}
    names = await resolve_user_names([bk.user_id for bk in upcoming])

    with ui.dialog() as dlg, ui.card().classes("w-[36rem]"):
        ui.label(t("migrate_bookings_title").format(name=res.name)).classes("text-h6")
        ui.label(t("migrate_bookings_hint")).classes("text-sm text-grey-8")

        selects = {}
        for bk in upcoming:
            with ui.row().classes("w-full items-center no-wrap"):
                who = names.get(bk.user_id, str(bk.user_id))
                ui.label(f"{who} — {_format_booking_period(bk.start_date, bk.end_date)}") \
                    .classes("grow text-sm")
                selects[bk.id] = ui.select(
                    options, label=t("migrate_target"),
                ).props("outlined dense stack-label").classes("w-56")

        async def confirm():
            if any(sel.value is None for sel in selects.values()):
                ui.notify(t("migrate_select_all"), color="negative")
                return
            try:
                for bk_id, sel in selects.items():
                    await migrate_booking(bk_id, uuid.UUID(sel.value), actor=user)
                await delete_resource(res.id, actor=user)
            except (BookingConflictError, BookingValidationError) as e:
                ui.notify(str(e), color="negative")
                return
            dlg.close()
            ui.notify(t("bookings_migrated_resource_deleted"), color="positive")
            await _render_bookings(outer_container, user)

        with ui.row():
            ui.button(t("cancel"), on_click=dlg.close).props("flat")
            ui.button(t("migrate_and_delete"), on_click=confirm).props("color=negative")
    dlg.open()


async def _show_resource_dialog(outer_container, user, resource=None, on_saved=None):
    editing = resource is not None

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label(t("edit_resource") if editing else t("add_resource")).classes("text-h6")

        name_input = ui.input(
            t("resource_name"), value=resource.name if editing else "",
        ).props("outlined dense").classes("w-full")

        type_select = ui.select(
            options=RESOURCE_TYPES,
            value=resource.resource_type if editing else RESOURCE_TYPES[0],
            label=t("resource_type"),
        ).props("outlined dense").classes("w-full")

        sites = [term.code for term in await resolve_terms("sites")]
        location_select = ui.select(
            options=sites,
            value=resource.location if editing and resource.location in sites else (sites[0] if sites else None),
            label=t("resource_location"),
        ).props("outlined dense").classes("w-full")

        desc_input = ui.textarea(
            t("description"),
            value=resource.description or "" if editing else "",
        ).props("outlined dense").classes("w-full")

        specs_container = ui.column().classes("w-full")
        owner_container = ui.column().classes("w-full")

        existing_specs = (resource.specs or {}) if editing else {}
        spec_inputs = {}
        with specs_container:
            ui.label(t("specs")).classes("text-subtitle2 mt-2")
            for key in ("cpu", "ram", "hdd", "gpu"):
                spec_inputs[key] = ui.input(
                    t(key), value=existing_specs.get(key, ""),
                ).props("outlined dense").classes("w-full")

        active_users = await _load_active_users()
        owner_options = {None: t("no_owner"), **{u.id: (u.full_name or u.email) for u in active_users}}
        with owner_container:
            owner_select = ui.select(
                owner_options,
                value=resource.owner_user_id if editing else None,
                label=t("resource_owner"),
            ).props("outlined dense with-input").classes("w-full")

        def _toggle_type_fields(resource_type: str):
            is_office = _office_fields_visible(resource_type)
            specs_container.set_visibility(not is_office)
            owner_container.set_visibility(is_office)

        _toggle_type_fields(type_select.value)
        type_select.on_value_change(lambda e: _toggle_type_fields(e.value))

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            async def do_save():
                if not name_input.value.strip():
                    ui.notify(t("required_field"), color="negative")
                    return
                is_office = _office_fields_visible(type_select.value)
                specs = None if is_office else (
                    {k: v.value.strip() for k, v in spec_inputs.items() if v.value.strip()} or None
                )
                owner_user_id = owner_select.value if is_office else None
                try:
                    if editing:
                        await update_resource(
                            resource.id,
                            actor=user,
                            name=name_input.value.strip(),
                            resource_type=type_select.value,
                            location=location_select.value,
                            description=desc_input.value.strip() or None,
                            specs=specs,
                            owner_user_id=owner_user_id,
                        )
                        ui.notify(t("resource_updated"), color="positive")
                    else:
                        await create_resource(
                            name=name_input.value.strip(),
                            resource_type=type_select.value,
                            description=desc_input.value.strip(),
                            location=location_select.value,
                            specs=specs,
                            owner_user_id=owner_user_id,
                            actor=user,
                        )
                        ui.notify(t("resource_created"), color="positive")
                except Exception as e:
                    ui.notify(str(e), color="negative")
                    return
                dialog.close()
                if on_saved is not None:
                    await on_saved()
                else:
                    await _render_bookings(outer_container, user)

            ui.button(t("save"), on_click=do_save).props("color=primary")

    dialog.open()


def _show_software_dialog(outer_container, user):
    """Admin dialog to manage OS choices and per-OS software tags."""

    async def _load_and_render():
        bc = await bookings_config.get()
        os_list = bc.os_choices
        sw_tags = bc.software_tags
        _render_dialog(os_list, dict(sw_tags))

    def _render_dialog(os_list, sw_tags):
        state = {"os_list": list(os_list), "sw_tags": sw_tags, "active_os": os_list[0] if os_list else None}

        with ui.dialog() as dialog, ui.card().classes("w-[600px]"):
            ui.label(t("manage_software")).classes("text-h6")

            # --- OS list ---
            ui.label(t("os")).classes("text-subtitle2 mt-2")
            os_container = ui.row().classes("flex-wrap gap-1")

            sw_label = ui.label("").classes("text-subtitle2 mt-3")
            sw_container = ui.column().classes("w-full gap-1")

            def _render_os_chips():
                os_container.clear()
                with os_container:
                    for os_name in state["os_list"]:
                        is_active = os_name == state["active_os"]
                        chip = ui.chip(
                            os_name,
                            color="primary" if is_active else "grey-3",
                            text_color="white" if is_active else "grey-8",
                            removable=True,
                        ).props("dense")

                        def select_os(_, name=os_name):
                            state["active_os"] = name
                            _render_os_chips()
                            _render_sw_list()

                        chip.on_click(select_os)

                        def remove_os(_, name=os_name):
                            state["os_list"].remove(name)
                            state["sw_tags"].pop(name, None)
                            if state["active_os"] == name:
                                state["active_os"] = state["os_list"][0] if state["os_list"] else None
                            _render_os_chips()
                            _render_sw_list()

                        chip.on_value_change(lambda e, name=os_name: remove_os(e, name) if not e.value else None)

                    # Add OS input
                    new_os = ui.input(placeholder=t("add_os")).props("outlined dense").classes("w-28")

                    def add_os():
                        name = new_os.value.strip()
                        if name and name not in state["os_list"]:
                            state["os_list"].append(name)
                            state["sw_tags"][name] = []
                            state["active_os"] = name
                            new_os.value = ""
                            _render_os_chips()
                            _render_sw_list()

                    new_os.on("keydown.enter", lambda _: add_os())

            def _render_sw_list():
                sw_container.clear()
                active = state["active_os"]
                if not active:
                    sw_label.text = ""
                    return
                sw_label.text = f"{t('software')} — {active}"
                tags = state["sw_tags"].get(active, [])
                with sw_container:
                    with ui.row().classes("flex-wrap gap-1"):
                        for tag in tags:
                            chip = ui.chip(tag, color="primary", text_color="white", removable=True).props("dense")

                            def remove_sw(_, sw=tag):
                                state["sw_tags"][state["active_os"]].remove(sw)
                                _render_sw_list()

                            chip.on_value_change(lambda e, sw=tag: remove_sw(e, sw) if not e.value else None)

                    # Add software input
                    with ui.row().classes("items-center gap-1"):
                        new_sw = ui.input(placeholder=t("add_software")).props("outlined dense").classes("w-48")

                        def add_sw():
                            name = new_sw.value.strip()
                            active = state["active_os"]
                            if name and active and name not in state["sw_tags"].get(active, []):
                                state["sw_tags"].setdefault(active, []).append(name)
                                new_sw.value = ""
                                _render_sw_list()

                        new_sw.on("keydown.enter", lambda _: add_sw())

            _render_os_chips()
            _render_sw_list()

            # --- Save / Cancel ---
            with ui.row().classes("justify-end gap-2 mt-3"):
                ui.button(t("cancel"), on_click=dialog.close).props("flat")

                async def do_save():
                    if not await has_permissions(user, "manage_bookings"):
                        ui.notify(t("access_denied"), color="negative")
                        return
                    bc = await bookings_config.get()
                    await bookings_config.set(bc.model_copy(update={
                        "os_choices": state["os_list"],
                        "software_tags": state["sw_tags"],
                    }))
                    ui.notify(t("settings_saved"), color="positive")
                    dialog.close()

                ui.button(t("save"), on_click=do_save).props("color=primary")

        dialog.open()

    ui.timer(0, _load_and_render, once=True)
