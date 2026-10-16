"""Headcounts by status, employer or team, over any dates.

A person counts in a period if one of their tenures covers at least one day
of it. Someone who changed status in a year counts in both rows of that year,
so `totals` (distinct people) is kept apart from the rows.
"""

import csv
import io
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import select

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.permissions import check_permission
from not_dot_net.backend.tenure_service import MANAGE_USERS, UserTenure, periods_overlap

Dimension = Literal["status", "employer", "team"]
Period = tuple[date, date]
CSV_COLUMNS = ("person_id", "name", "email", "status", "employer", "team", "start_date", "end_date")


@dataclass(frozen=True)
class HeadcountTable:
    periods: list[Period]
    rows: dict[str, list[int]]  # group value ("" when unknown) -> one count per period
    totals: list[int]


def year_periods(first_year: int, last_year: int) -> list[Period]:
    return [(date(y, 1, 1), date(y, 12, 31)) for y in range(first_year, last_year + 1)]


def count_by(tenures, periods: list[Period], by: Dimension) -> HeadcountTable:
    # simplify: scans every tenure for every period, fine for a lab's few thousand
    # rows; push the overlap test into SQL if it ever grows to six figures.
    present = [[t for t in tenures if periods_overlap(t.start_date, t.end_date, *p)] for p in periods]
    groups = sorted({getattr(t, by) or "" for column in present for t in column}, key=lambda g: (g == "", g))
    rows = {
        g: [len({t.user_id for t in column if (getattr(t, by) or "") == g}) for column in present]
        for g in groups
    }
    return HeadcountTable(periods, rows, [len({t.user_id for t in column}) for column in present])


async def headcount(periods: list[Period], by: Dimension, actor) -> HeadcountTable:
    await check_permission(actor, MANAGE_USERS)
    async with session_scope() as session:
        tenures = (await session.scalars(select(UserTenure))).all()
    return count_by(tenures, periods, by)


async def tenures_csv(actor) -> str:
    """Every tenure as a raw dated row, for analyses the page doesn't offer."""
    await check_permission(actor, MANAGE_USERS)
    async with session_scope() as session:
        rows = (await session.execute(
            select(UserTenure, User.full_name, User.email)
            .join(User, User.id == UserTenure.user_id)
            .order_by(User.full_name, UserTenure.start_date)
        )).all()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    for tenure, name, email in rows:
        writer.writerow([
            tenure.user_id, name or "", email, tenure.status, tenure.employer, tenure.team or "",
            tenure.start_date.isoformat(), tenure.end_date.isoformat() if tenure.end_date else "",
        ])
    return out.getvalue()
