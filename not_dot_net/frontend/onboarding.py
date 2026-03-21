from contextlib import asynccontextmanager
from datetime import date

from nicegui import ui

from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.onboarding import OnboardingRequest
from not_dot_net.config import get_settings
from not_dot_net.frontend.i18n import t
from sqlalchemy import select


def render(current_user: User):
    ui.label(t("new_person")).classes("text-h6 mb-2")

    settings = get_settings()
    team_options = settings.teams
    status_keys = ["researcher", "phd_student", "intern", "visitor"]
    status_options = {key: t(key) for key in status_keys}

    name_input = ui.input(t("name")).props("outlined dense").classes("w-full")
    email_input = ui.input(t("email")).props("outlined dense").classes("w-full")
    role_select = ui.select(status_options, label=t("role_status")).props(
        "outlined dense"
    ).classes("w-full")
    team_select = ui.select(team_options, label=t("team")).props(
        "outlined dense"
    ).classes("w-full")
    date_input = ui.date(value=date.today().isoformat()).classes("w-full")
    note_input = ui.textarea(t("note_optional")).props("outlined dense").classes("w-full")

    request_list = ui.column().classes("w-full mt-4")

    async def load_requests():
        get_session_ctx = asynccontextmanager(get_async_session)
        async with get_session_ctx() as session:
            if current_user.is_superuser:
                stmt = select(OnboardingRequest).order_by(
                    OnboardingRequest.created_at.desc()
                )
            else:
                stmt = (
                    select(OnboardingRequest)
                    .where(OnboardingRequest.created_by == current_user.id)
                    .order_by(OnboardingRequest.created_at.desc())
                )
            result = await session.execute(stmt)
            return result.scalars().all()

    async def refresh_list():
        requests = await load_requests()
        request_list.clear()
        with request_list:
            if not requests:
                ui.label(t("no_requests")).classes("text-gray-500")
                return
            ui.label(t("onboarding_requests")).classes("text-h6 mt-2")
            for req in requests:
                with ui.card().classes("w-full"):
                    with ui.row().classes("items-center justify-between w-full"):
                        ui.label(f"{req.person_name} ({req.person_email})").classes(
                            "font-bold"
                        )
                        ui.badge(req.status).props(
                            "color=orange" if req.status == "pending" else "color=green"
                        )
                    role_display = t(req.role_status) if req.role_status else ""
                    ui.label(
                        f"{role_display} · {req.team} · {t('starts', date=req.start_date)}"
                    ).classes("text-sm text-gray-500")
                    if req.note:
                        ui.label(req.note).classes("text-sm")

    async def submit():
        if not name_input.value or not email_input.value:
            ui.notify(t("name_email_required"), color="negative")
            return

        get_session_ctx = asynccontextmanager(get_async_session)
        async with get_session_ctx() as session:
            request = OnboardingRequest(
                created_by=current_user.id,
                person_name=name_input.value,
                person_email=email_input.value,
                role_status=role_select.value or "",
                team=team_select.value or "",
                start_date=date.fromisoformat(date_input.value) if date_input.value else date.today(),
                note=note_input.value or None,
            )
            session.add(request)
            await session.commit()

        ui.notify(t("onboarding_created"), color="positive")
        name_input.value = ""
        email_input.value = ""
        role_select.value = None
        team_select.value = None
        note_input.value = ""
        await refresh_list()

    ui.button(t("submit"), on_click=submit, icon="send").props("color=primary").classes(
        "mt-2"
    )

    ui.timer(0, refresh_list, once=True)
