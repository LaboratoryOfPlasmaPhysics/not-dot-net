"""A tenure records the team too, so headcounts per team can be taken at any date."""

import uuid
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from not_dot_net.backend.data_io import export_tenures, import_tenures
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import SYSTEM_ACTOR
from not_dot_net.backend.personnel_import import import_personnel, parse_clean_rows
from not_dot_net.backend.tenure_service import add_tenure, list_tenures, update_tenure
from not_dot_net.backend.workflow_models import RequestStatus, WorkflowRequest
from not_dot_net.backend.workflow_service import _record_tenure
from not_dot_net.config import TenureHookConfig
from tests.test_personnel_import import _row, _users


async def _user(email="team@test.com", team=None) -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role="staff", team=team)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def test_a_tenure_keeps_its_team():
    user = await _user()
    await add_tenure(user.id, "PhD", "CNRS", date(2024, 9, 1), team="Space Weather", actor=SYSTEM_ACTOR)
    [tenure] = await list_tenures(user.id)
    assert tenure.team == "Space Weather"


async def test_the_team_is_optional():
    user = await _user()
    tenure = await add_tenure(user.id, "PhD", "CNRS", date(2024, 9, 1), actor=SYSTEM_ACTOR)
    assert tenure.team is None


async def test_fixing_a_team_edits_the_tenure_in_place():
    user = await _user()
    tenure = await add_tenure(user.id, "PhD", "CNRS", date(2024, 9, 1), team="Theory", actor=SYSTEM_ACTOR)
    await update_tenure(tenure.id, team="Space Weather", actor=SYSTEM_ACTOR)
    assert (await list_tenures(user.id))[0].team == "Space Weather"
    await update_tenure(tenure.id, status="CDD", actor=SYSTEM_ACTOR)
    assert (await list_tenures(user.id))[0].team == "Space Weather"


async def test_export_and_import_carry_the_team():
    user = await _user("roundtrip@test.com")
    await add_tenure(user.id, "PhD", "CNRS", date(2024, 9, 1), team="Theory", actor=SYSTEM_ACTOR)
    [row] = await export_tenures()
    assert row["team"] == "Theory"
    row.update(team="Space Weather")
    assert (await import_tenures([row], replace=True))["updated"] == 1
    assert (await list_tenures(user.id))[0].team == "Space Weather"


async def test_archive_import_keeps_each_contracts_team():
    await import_personnel(parse_clean_rows([
        _row(team="Theory", start_date="2015-03-01", end_date="2015-08-31"),
        _row(team="Space Weather", status="PhD", start_date="2016-10-01", end_date="2019-09-30"),
    ]))
    [user] = await _users()
    assert [t.team for t in await list_tenures(user.id)] == ["Theory", "Space Weather"]


async def test_re_running_the_archive_import_fills_missing_teams():
    """Tenures imported before teams were stored get theirs on the next run."""
    await import_personnel(parse_clean_rows([_row(team="")]))
    summary = await import_personnel(parse_clean_rows([_row(team="Theory")]))
    [user] = await _users()
    [tenure] = await list_tenures(user.id)
    assert tenure.team == "Theory"
    assert summary.tenures_created == 0


async def test_re_import_does_not_overwrite_a_team_fixed_in_the_app():
    await import_personnel(parse_clean_rows([_row(team="Theory")]))
    [user] = await _users()
    [tenure] = await list_tenures(user.id)
    await update_tenure(tenure.id, team="Space Weather", actor=SYSTEM_ACTOR)
    await import_personnel(parse_clean_rows([_row(team="Theory")]))
    assert (await list_tenures(user.id))[0].team == "Space Weather"


async def _completed_onboarding(user, data) -> WorkflowRequest:
    async with session_scope() as session:
        req = WorkflowRequest(
            type="onboarding", current_step="done", status=RequestStatus.COMPLETED,
            data=data, created_by=user.id,
        )
        session.add(req)
        await session.commit()
        await session.refresh(req)
        return req


async def test_onboarding_records_the_team_from_the_request():
    user = await _user("newcomer-team@test.com", team="Administration")
    req = await _completed_onboarding(user, {"status": "PhD", "employer": "CNRS", "team": "Theory"})
    await _record_tenure(req, TenureHookConfig(), user.id)
    assert (await list_tenures(user.id))[0].team == "Theory"


async def test_onboarding_without_a_team_field_uses_the_persons_team():
    user = await _user("newcomer-noteam@test.com", team="Administration")
    req = await _completed_onboarding(user, {"status": "PhD", "employer": "CNRS"})
    await _record_tenure(req, TenureHookConfig(), user.id)
    assert (await list_tenures(user.id))[0].team == "Administration"


async def test_migration_gives_open_tenures_todays_team_only(tmp_path):
    """A closed tenure's team is unknown: copying today's would invent history."""
    from alembic import command
    from not_dot_net.backend.migrate import _alembic_config, bootstrap_schema

    url = f"sqlite+aiosqlite:///{tmp_path / 'team.db'}"
    await bootstrap_schema(url)
    cfg = _alembic_config(url)
    command.downgrade(cfg, "0023")
    engine = create_async_engine(url, poolclass=NullPool)
    uid = uuid.uuid4().hex
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "insert into user (id, email, hashed_password, is_active, is_superuser, is_verified, auth_method, role, team) "
                "values (:id, 'm@test.com', 'x', 1, 0, 0, 'LOCAL', '', 'Theory')"), {"id": uid})
            for tid, start, end in (("a" * 32, "2016-01-01", "2019-12-31"), ("b" * 32, "2020-01-01", None)):
                await conn.execute(text(
                    "insert into user_tenure (id, user_id, status, employer, start_date, end_date) "
                    "values (:tid, :uid, 'PhD', 'CNRS', :start, :end)"),
                    {"tid": tid, "uid": uid, "start": start, "end": end})
        command.upgrade(cfg, "head")
        async with engine.connect() as conn:
            rows = dict((await conn.execute(text("select id, team from user_tenure"))).all())
    finally:
        await engine.dispose()
    assert rows == {"a" * 32: None, "b" * 32: "Theory"}


async def test_the_person_page_shows_and_edits_the_tenure_team(user):
    from nicegui import ui
    from not_dot_net.backend.vocabularies import (
        StoredVocabulary, VocabulariesConfig, VocabularyTerm, vocabularies_config,
    )
    from not_dot_net.frontend.directory import _render_detail

    await vocabularies_config.set(VocabulariesConfig(vocabularies={"teams": StoredVocabulary(
        key="teams", label={"en": "Teams"},
        terms=[VocabularyTerm(code=c, labels={"en": c}) for c in ("Theory", "Space Weather")],
    )}))
    async with session_scope() as session:
        person = User(email="team-ui@test.com", hashed_password="x", is_active=True, is_superuser=True)
        session.add(person)
        await session.commit()
        await session.refresh(person)
    await add_tenure(person.id, "PhD", "CNRS", date(2025, 10, 1), team="Theory", actor=SYSTEM_ACTOR)

    @ui.page("/_tenure_team")
    async def page():
        await _render_detail(ui.column(), person, person, {"details": {}, "expanded_id": None})

    await user.open("/_tenure_team")
    user.find("Employment History").click()
    await user.should_see("Theory")
    user.find(marker="edit-tenure").click()
    await user.should_see(marker="tenure-team")
    user.find(marker="tenure-team").elements.pop().set_value("Space Weather")
    user.find("Save").click()
    await user.should_see(kind=ui.label, content="Space Weather")
    assert (await list_tenures(person.id))[0].team == "Space Weather"
