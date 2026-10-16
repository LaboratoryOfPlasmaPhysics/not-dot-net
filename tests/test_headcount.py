"""Headcounts by status, employer or team, for any date or period."""

import csv
import io
import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.headcount import count_by, headcount, tenures_csv, year_periods
from not_dot_net.backend.permissions import SYSTEM_ACTOR
from not_dot_net.backend.tenure_service import add_tenure


def _tenure(user, status, start, end=None, employer="CNRS", team=None):
    return SimpleNamespace(user_id=user, status=status, employer=employer, team=team,
                           start_date=start, end_date=end)


ALICE, BOB = uuid.uuid4(), uuid.uuid4()
YEAR_2023 = (date(2023, 1, 1), date(2023, 12, 31))


def test_a_single_date_counts_who_was_there_that_day():
    tenures = [
        _tenure(ALICE, "PhD", date(2022, 10, 1)),
        _tenure(BOB, "PhD", date(2020, 1, 1), date(2022, 12, 31)),
    ]
    day = (date(2023, 3, 1), date(2023, 3, 1))
    table = count_by(tenures, [day], "status")
    assert table.rows == {"PhD": [1]}
    assert table.totals == [1]


def test_a_period_counts_anyone_present_at_least_one_day():
    tenures = [_tenure(BOB, "PhD", date(2020, 1, 1), date(2023, 1, 1))]
    assert count_by(tenures, [YEAR_2023], "status").rows == {"PhD": [1]}


def test_a_status_change_counts_in_both_rows_but_once_in_the_total():
    tenures = [
        _tenure(ALICE, "Intern", date(2023, 3, 1), date(2023, 8, 31)),
        _tenure(ALICE, "PhD", date(2023, 10, 1)),
    ]
    table = count_by(tenures, [YEAR_2023], "status")
    assert table.rows == {"Intern": [1], "PhD": [1]}
    assert table.totals == [1]


def test_one_column_per_period():
    tenures = [_tenure(ALICE, "PhD", date(2023, 10, 1), date(2024, 3, 31))]
    table = count_by(tenures, year_periods(2022, 2025), "status")
    assert table.rows == {"PhD": [0, 1, 1, 0]}
    assert table.totals == [0, 1, 1, 0]


def test_group_by_team_puts_a_missing_team_in_its_own_row():
    tenures = [
        _tenure(ALICE, "PhD", date(2023, 1, 1), team="Theory"),
        _tenure(BOB, "PhD", date(2023, 1, 1)),
    ]
    rows = count_by(tenures, [YEAR_2023], "team").rows
    assert rows == {"Theory": [1], "": [1]}
    assert list(rows) == ["Theory", ""]  # unknown last, not first


def test_group_by_employer():
    tenures = [
        _tenure(ALICE, "PhD", date(2023, 1, 1), employer="CNRS"),
        _tenure(BOB, "PhD", date(2023, 1, 1), employer="CNES"),
    ]
    assert count_by(tenures, [YEAR_2023], "employer").rows == {"CNES": [1], "CNRS": [1]}


def test_year_periods_cover_whole_years():
    assert year_periods(2023, 2024) == [
        (date(2023, 1, 1), date(2023, 12, 31)), (date(2024, 1, 1), date(2024, 12, 31)),
    ]


async def _person(email, name):
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role="staff", full_name=name)
        session.add(user)
        await session.commit()
        return user


async def test_headcount_reads_the_recorded_tenures():
    person = await _person("hc@test.com", "Marie Curie")
    await add_tenure(person.id, "PhD", "CNRS", date(2023, 1, 1), team="Theory", actor=SYSTEM_ACTOR)
    table = await headcount([YEAR_2023], "team", actor=SYSTEM_ACTOR)
    assert table.rows == {"Theory": [1]}


async def test_headcounts_and_the_export_need_manage_users():
    with pytest.raises(PermissionError):
        await headcount([YEAR_2023], "status", actor=None)
    with pytest.raises(PermissionError):
        await tenures_csv(actor=None)


async def test_the_export_has_one_line_per_tenure_with_raw_dates():
    """Raw rows, so any counting rule can be applied afterwards."""
    person = await _person("csv@test.com", "Marie Curie")
    await add_tenure(person.id, "Intern", "CNRS", date(2022, 3, 1), date(2022, 8, 31), actor=SYSTEM_ACTOR)
    await add_tenure(person.id, "PhD", "CNRS", date(2022, 10, 1), team="Theory", actor=SYSTEM_ACTOR)
    rows = list(csv.DictReader(io.StringIO(await tenures_csv(actor=SYSTEM_ACTOR))))
    assert [r["status"] for r in rows] == ["Intern", "PhD"]
    assert rows[1] == {
        "person_id": str(person.id), "name": "Marie Curie", "email": "csv@test.com",
        "status": "PhD", "employer": "CNRS", "team": "Theory",
        "start_date": "2022-10-01", "end_date": "",
    }
