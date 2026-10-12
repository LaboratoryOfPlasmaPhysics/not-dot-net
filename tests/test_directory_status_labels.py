"""The directory shows status labels, not vocabulary codes (review 2026-10-12 V-list)."""

from datetime import date

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import SYSTEM_ACTOR
from not_dot_net.backend.tenure_service import add_tenure
from not_dot_net.backend.vocabularies import (
    StoredVocabulary, VocabulariesConfig, VocabularyTerm, vocabularies_config,
)
from not_dot_net.frontend.directory import _render_detail


async def test_status_and_tenure_chip_use_the_term_label(user: UiUser):
    await vocabularies_config.set(VocabulariesConfig(vocabularies={
        "employment_statuses": StoredVocabulary(
            key="employment_statuses", label={"en": "Statuses"},
            terms=[VocabularyTerm(code="phd_cdd", labels={"en": "PhD student"})],
        ),
    }))
    async with session_scope() as session:
        person = User(email="phd@test.com", hashed_password="x", is_active=True,
                      is_superuser=True, employment_status="phd_cdd")
        session.add(person)
        await session.commit()
        await session.refresh(person)
    await add_tenure(person.id, "phd_cdd", "CNRS", date(2025, 10, 1), actor=SYSTEM_ACTOR)

    @ui.page("/_status_labels")
    async def page():
        await _render_detail(ui.column(), person, person, {"details": {}, "expanded_id": None})

    await user.open("/_status_labels")
    await user.should_see("Status: PhD student")
    user.find("Employment History").click()
    await user.should_see(kind=ui.chip, content="PhD student")
    await user.should_not_see("phd_cdd")
