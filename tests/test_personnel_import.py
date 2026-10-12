"""Tests for the server-side clean-CSV personnel importer (-> User + UserTenure).

The server only ingests a clean, canonical CSV. The messy Access-archive
cleaning is a separate one-off script (see scripts/clean_personnel_archive.py).
"""

import uuid
from contextlib import asynccontextmanager
from datetime import date

import pytest
from sqlalchemy import select

from not_dot_net.backend.db import User, get_async_session
from not_dot_net.backend.personnel_import import (
    ContractRecord,
    ImportSummary,
    import_personnel,
    parse_clean_csv_text,
    parse_clean_rows,
)
from not_dot_net.backend.tenure_service import UserTenure


def _row(**overrides) -> dict:
    """A row of the clean canonical CSV schema."""
    row = {
        "first_name": "Marie",
        "last_name": "Dupont",
        "email": "marie.dupont@lpp.polytechnique.fr",
        "employer": "CNRS",
        "team": "Plasmas Spatiaux",
        "status": "Intern",
        "start_date": "2015-03-01",
        "end_date": "2015-08-31",
        "notes": "Stage / Stagiaire",
    }
    row.update(overrides)
    return row


# ---- parsing the clean CSV ------------------------------------------------


def test_parse_clean_rows_maps_fields():
    [rec] = parse_clean_rows([_row()])
    assert isinstance(rec, ContractRecord)
    assert rec.first_name == "Marie"
    assert rec.last_name == "Dupont"
    assert rec.email == "marie.dupont@lpp.polytechnique.fr"
    assert rec.employer == "CNRS"
    assert rec.team == "Plasmas Spatiaux"
    assert rec.status == "Intern"
    assert rec.start_date == date(2015, 3, 1)
    assert rec.end_date == date(2015, 8, 31)


def test_parse_clean_rows_blank_end_date_is_open():
    [rec] = parse_clean_rows([_row(end_date="")])
    assert rec.end_date is None


def test_parse_clean_rows_rejects_missing_start_date():
    with pytest.raises(ValueError, match="start_date"):
        parse_clean_rows([_row(start_date="")])


def test_parse_clean_rows_rejects_non_iso_date():
    with pytest.raises(ValueError, match="start_date"):
        parse_clean_rows([_row(start_date="01/03/2015")])


def test_parse_clean_rows_normalizes_email_case():
    [rec] = parse_clean_rows([_row(email="Marie.DUPONT@LPP.fr")])
    assert rec.email == "marie.dupont@lpp.fr"


def test_parse_clean_csv_text():
    text = (
        "first_name,last_name,email,employer,team,status,start_date,end_date,notes\n"
        "Marie,Dupont,marie@lpp.fr,CNRS,Spatiaux,Intern,2015-03-01,2015-08-31,\n"
    )
    [rec] = parse_clean_csv_text(text)
    assert rec.first_name == "Marie"
    assert rec.status == "Intern"
    assert rec.start_date == date(2015, 3, 1)


def test_parse_clean_csv_text_propagates_validation_error():
    text = (
        "first_name,last_name,status,start_date\n"
        "Marie,Dupont,Intern,not-a-date\n"
    )
    with pytest.raises(ValueError, match="start_date"):
        parse_clean_csv_text(text)


# ---- import into User + UserTenure ----------------------------------------


async def _users():
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        return (await session.execute(select(User))).scalars().all()


async def _tenures(user_id):
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        return (
            await session.execute(
                select(UserTenure).where(UserTenure.user_id == user_id).order_by(UserTenure.start_date)
            )
        ).scalars().all()


async def test_import_creates_inactive_user_with_tenure():
    summary = await import_personnel(parse_clean_rows([_row()]))
    assert isinstance(summary, ImportSummary)
    assert summary.people_created == 1
    assert summary.tenures_created == 1

    [user] = await _users()
    assert user.is_active is False
    assert user.email == "marie.dupont@lpp.polytechnique.fr"
    assert user.full_name == "Marie Dupont"
    [tenure] = await _tenures(user.id)
    assert tenure.status == "Intern"
    assert tenure.employer == "CNRS"
    assert tenure.start_date == date(2015, 3, 1)
    assert tenure.end_date == date(2015, 8, 31)


async def test_import_records_consecutive_contracts_as_multiple_tenures():
    rows = [
        _row(status="Intern", start_date="2015-03-01", end_date="2015-08-31"),
        _row(status="PhD", start_date="2016-10-01", end_date="2019-09-30"),
        _row(status="PostDoc", start_date="2020-01-01", end_date=""),
    ]
    summary = await import_personnel(parse_clean_rows(rows))
    assert summary.people_created == 1
    assert summary.tenures_created == 3

    [user] = await _users()
    tenures = await _tenures(user.id)
    assert [t.status for t in tenures] == ["Intern", "PhD", "PostDoc"]
    assert tenures[-1].end_date is None


async def test_import_attaches_to_existing_user_by_email():
    get_session = asynccontextmanager(get_async_session)
    async with get_session() as session:
        existing = User(
            id=uuid.uuid4(),
            email="marie.dupont@lpp.polytechnique.fr",
            hashed_password="x",
            is_active=True,
            role="staff",
        )
        session.add(existing)
        await session.commit()
        existing_id = existing.id

    summary = await import_personnel(parse_clean_rows([_row()]))
    assert summary.people_created == 0
    assert summary.people_matched == 1

    [user] = await _users()
    assert user.id == existing_id
    assert user.is_active is True  # existing AD user untouched
    assert len(await _tenures(existing_id)) == 1


async def test_import_is_idempotent():
    await import_personnel(parse_clean_rows([_row()]))
    second = await import_personnel(parse_clean_rows([_row()]))
    assert second.people_created == 0
    assert second.tenures_created == 0

    [user] = await _users()
    assert len(await _tenures(user.id)) == 1


async def test_import_idempotent_for_rows_with_empty_employer():
    rows = [_row(employer="")]
    await import_personnel(parse_clean_rows(rows))
    second = await import_personnel(parse_clean_rows(rows))
    assert second.tenures_created == 0
    [user] = await _users()
    assert len(await _tenures(user.id)) == 1


async def test_import_synthesizes_email_when_blank():
    summary = await import_personnel(parse_clean_rows([_row(email="")]))
    assert summary.people_created == 1
    [user] = await _users()
    assert user.email == "marie.dupont@archive.invalid"


# ---- F11 (review 2026-10-12) ----------------------------------------------


def test_parse_rejects_end_before_start():
    with pytest.raises(ValueError, match="row 2: end_date"):
        parse_clean_rows([_row(start_date="2015-08-31", end_date="2015-03-01")])


async def test_homonyms_with_different_emails_stay_two_people():
    rows = [
        _row(email="marie.dupont@lpp.polytechnique.fr"),
        _row(email="marie.dupont2@cnrs.fr", start_date="2019-01-01", end_date="2019-12-31"),
    ]
    summary = await import_personnel(parse_clean_rows(rows))
    assert summary.people_created == 2
    assert sorted(u.email for u in await _users()) == [
        "marie.dupont2@cnrs.fr", "marie.dupont@lpp.polytechnique.fr",
    ]


async def test_rows_without_email_join_the_only_emailed_homonym():
    rows = [
        _row(),
        _row(email="", start_date="2016-01-01", end_date="2016-06-30"),
    ]
    summary = await import_personnel(parse_clean_rows(rows))
    assert summary.people_created == 1
    [user] = await _users()
    assert len(await _tenures(user.id)) == 2


async def test_overlapping_contracts_are_skipped_and_counted():
    rows = [
        _row(),
        _row(start_date="2015-06-01", end_date="2015-12-31", status="PhD"),
    ]
    summary = await import_personnel(parse_clean_rows(rows))
    assert summary.tenures_created == 1
    assert summary.tenures_skipped_overlap == 1
    assert "overlap" in str(summary)


async def test_names_with_the_same_slug_get_distinct_synthetic_emails():
    rows = [
        _row(first_name="Jean Marc", last_name="Petit", email=""),
        _row(first_name="Jean-Marc", last_name="Petit", email=""),
    ]
    summary = await import_personnel(parse_clean_rows(rows))
    assert summary.people_created == 2
    assert len({u.email for u in await _users()}) == 2

    again = await import_personnel(parse_clean_rows(rows))
    assert again.people_created == 0
    assert again.people_matched == 2
