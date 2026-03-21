from contextlib import asynccontextmanager

from nicegui import ui

from not_dot_net.backend.db import User, get_async_session, get_user_db
from not_dot_net.backend.schemas import UserUpdate
from not_dot_net.backend.users import get_user_manager
from sqlalchemy import select


async def _load_people() -> list[User]:
    get_session_ctx = asynccontextmanager(get_async_session)
    async with get_session_ctx() as session:
        result = await session.execute(select(User).where(User.is_active == True))  # noqa: E712
        return result.scalars().all()


async def _update_user(user_id, updates: dict):
    """Update a user via UserManager (respects FastAPI-Users hooks)."""
    get_session_ctx = asynccontextmanager(get_async_session)
    get_user_db_ctx = asynccontextmanager(get_user_db)
    get_user_manager_ctx = asynccontextmanager(get_user_manager)
    async with get_session_ctx() as session:
        async with get_user_db_ctx(session) as user_db:
            async with get_user_manager_ctx(user_db) as manager:
                user = await manager.get(user_id)
                update_schema = UserUpdate(**updates)
                await manager.update(update_schema, user)


async def _delete_user(user_id):
    """Delete a user via UserManager (respects FastAPI-Users hooks)."""
    get_session_ctx = asynccontextmanager(get_async_session)
    get_user_db_ctx = asynccontextmanager(get_user_db)
    get_user_manager_ctx = asynccontextmanager(get_user_manager)
    async with get_session_ctx() as session:
        async with get_user_db_ctx(session) as user_db:
            async with get_user_manager_ctx(user_db) as manager:
                user = await manager.get(user_id)
                await manager.delete(user)


def render(current_user: User):
    search = ui.input(placeholder="Search by name, team, office, email...").props(
        "outlined dense"
    ).classes("w-full mb-4")

    card_container = ui.element("div").classes(
        "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 w-full"
    )

    state = {"expanded_id": None, "details": {}}

    async def refresh():
        people = await _load_people()
        state["expanded_id"] = None
        state["details"] = {}
        card_container.clear()
        with card_container:
            for person in people:
                _person_card(person, current_user, state)

    def filter_cards():
        query = search.value.lower() if search.value else ""
        for child in card_container.default_slot.children:
            if hasattr(child, "_person_search_text"):
                child.set_visibility(query in child._person_search_text)

    search.on("update:model-value", lambda: filter_cards())

    ui.timer(0, refresh, once=True)


def _person_card(person: User, current_user: User, state: dict):
    display_name = person.full_name or person.email
    search_text = " ".join(
        s.lower() for s in [
            person.full_name or "", person.email,
            person.team or "", person.office or "",
        ]
    )

    with ui.card().classes("cursor-pointer") as card:
        card._person_search_text = search_text

        with ui.row().classes("items-center gap-3"):
            ui.icon("person", size="xl").classes(
                "rounded-full bg-gray-200 p-2"
            )
            with ui.column().classes("gap-0"):
                ui.label(display_name).classes("font-bold")
                if person.team:
                    ui.label(person.team).classes("text-sm text-gray-500")
                if person.office:
                    ui.label(f"Office {person.office}").classes("text-sm text-gray-500")

        detail_container = ui.column().classes("w-full mt-2")
        detail_container.set_visibility(False)
        state["details"][person.id] = detail_container

        def toggle_expand():
            currently_expanded = state["expanded_id"]
            if currently_expanded == person.id:
                detail_container.set_visibility(False)
                state["expanded_id"] = None
            else:
                if currently_expanded and currently_expanded in state["details"]:
                    state["details"][currently_expanded].set_visibility(False)
                detail_container.set_visibility(True)
                state["expanded_id"] = person.id
                _render_detail(detail_container, person, current_user, state)

        card.on("click", toggle_expand)


def _render_detail(container, person: User, current_user: User, state: dict):
    container.clear()
    is_own = person.id == current_user.id
    is_admin = current_user.is_superuser

    with container:
        ui.separator()
        if person.phone:
            ui.label(f"Phone: {person.phone}").classes("text-sm")
        ui.label(f"Email: {person.email}").classes("text-sm")
        if person.employment_status:
            ui.label(f"Status: {person.employment_status}").classes("text-sm")
        if person.title:
            ui.label(f"Title: {person.title}").classes("text-sm")

        if is_own or is_admin:
            ui.button("Edit", icon="edit", on_click=lambda: _render_edit(
                container, person, current_user, state
            )).props("flat dense")

        if is_admin and not is_own:
            with ui.dialog() as confirm_dialog, ui.card():
                ui.label(f"Delete {person.full_name or person.email}?")
                with ui.row():
                    ui.button("Cancel", on_click=confirm_dialog.close).props("flat")

                    async def do_delete():
                        confirm_dialog.close()
                        await _delete_user(person.id)
                        ui.notify(
                            f"Deleted {person.full_name or person.email}",
                            color="positive",
                        )
                        container.parent_slot.parent.set_visibility(False)

                    ui.button("Delete", on_click=do_delete).props(
                        "flat color=negative"
                    )

            ui.button("Delete", icon="delete", on_click=confirm_dialog.open).props(
                "flat dense color=negative"
            )


def _render_edit(container, person: User, current_user: User, state: dict):
    container.clear()
    is_admin = current_user.is_superuser

    with container:
        ui.separator()

        fields = {}
        if is_admin:
            fields["full_name"] = ui.input(
                "Full Name", value=person.full_name or ""
            ).props("outlined dense")
            fields["email"] = ui.input(
                "Email", value=person.email
            ).props("outlined dense")
            fields["team"] = ui.input(
                "Team", value=person.team or ""
            ).props("outlined dense")
            fields["employment_status"] = ui.input(
                "Status", value=person.employment_status or ""
            ).props("outlined dense")
            fields["title"] = ui.input(
                "Title", value=person.title or ""
            ).props("outlined dense")

        fields["phone"] = ui.input(
            "Phone", value=person.phone or ""
        ).props("outlined dense")
        fields["office"] = ui.input(
            "Office", value=person.office or ""
        ).props("outlined dense")

        async def save():
            updates = {k: (v.value or None) for k, v in fields.items()}
            await _update_user(person.id, updates)
            ui.notify("Saved", color="positive")
            people = await _load_people()
            updated = next((p for p in people if p.id == person.id), person)
            _render_detail(container, updated, current_user, state)

        with ui.row():
            ui.button("Save", on_click=save).props("flat dense color=primary")
            ui.button("Cancel", on_click=lambda: _render_detail(
                container, person, current_user, state
            )).props("flat dense")
