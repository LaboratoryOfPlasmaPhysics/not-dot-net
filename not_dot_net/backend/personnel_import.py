"""Import personnel contract history from a clean CSV into User + UserTenure.

The server only ingests a clean, canonical CSV with these columns:

    first_name, last_name, email, employer, team, status, start_date, end_date, notes

- dates are ISO `YYYY-MM-DD`; an empty `end_date` means the contract is open;
- `status` is already one of the "employment_statuses" vocabulary;
- `email` may be blank (a synthetic `@archive.invalid` address is generated).

Each distinct person becomes one inactive local `User` (kept out of the
AD-backed directory, which lists only active users) and each row becomes one
`UserTenure` — the consecutive-contract history.

The messy Access-archive (.mdb) -> clean-CSV conversion is a separate one-off
script (scripts/clean_personnel_archive.py); the server never parses .mdb.
"""

import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select

from not_dot_net.backend.db import AuthMethod, User, session_scope
from not_dot_net.backend.tenure_service import UserTenure, periods_overlap

# Imported ex-staff cannot log in: no usable password, inactive account.
NO_LOGIN_PASSWORD = "!imported-archive-no-login"
REQUIRED_COLUMNS = ("first_name", "last_name", "status", "start_date")


@dataclass(frozen=True)
class ContractRecord:
    first_name: str
    last_name: str
    email: str
    employer: str
    team: str
    status: str
    start_date: date
    end_date: date | None
    notes: str


def _clean(row: dict, key: str) -> str:
    return (row.get(key) or "").strip()


def _iso_date(value: str, *, field: str, line: int) -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"row {line}: {field} must be ISO YYYY-MM-DD, got {value!r}") from None


def parse_clean_rows(rows: Iterable[dict]) -> list[ContractRecord]:
    """Validate and build ContractRecords from clean CSV rows."""
    records = []
    for line, row in enumerate(rows, start=2):  # line 1 is the header
        missing = [c for c in REQUIRED_COLUMNS if not _clean(row, c)]
        if missing:
            raise ValueError(f"row {line}: missing required column(s): {', '.join(missing)}")
        start = _iso_date(_clean(row, "start_date"), field="start_date", line=line)
        end = _iso_date(_clean(row, "end_date"), field="end_date", line=line)
        if end is not None and end < start:
            raise ValueError(f"row {line}: end_date {end} is before start_date {start}")
        records.append(
            ContractRecord(
                first_name=_clean(row, "first_name"),
                last_name=_clean(row, "last_name"),
                email=_clean(row, "email").lower(),
                employer=_clean(row, "employer"),
                team=_clean(row, "team"),
                status=_clean(row, "status"),
                start_date=start,
                end_date=end,
                notes=_clean(row, "notes"),
            )
        )
    return records


def parse_clean_csv_text(text: str) -> list[ContractRecord]:
    return parse_clean_rows(csv.DictReader(io.StringIO(text)))


def load_clean_csv(path: str) -> list[ContractRecord]:
    with open(path, newline="", encoding="utf-8") as f:
        return parse_clean_rows(csv.DictReader(f))


@dataclass
class ImportSummary:
    rows_total: int = 0
    people_created: int = 0
    people_matched: int = 0
    tenures_created: int = 0
    tenures_skipped_existing: int = 0
    tenures_skipped_overlap: int = 0

    def __str__(self) -> str:
        return (
            f"{self.rows_total} contract rows | "
            f"people: +{self.people_created} created, {self.people_matched} matched | "
            f"tenures: +{self.tenures_created} created, {self.tenures_skipped_existing} already present, "
            f"{self.tenures_skipped_overlap} skipped (overlap)"
        )


def _person_key(rec: ContractRecord) -> tuple[str, str]:
    return (rec.last_name.upper(), rec.first_name.upper())


def _full_name(rec: ContractRecord) -> str:
    return f"{rec.first_name} {rec.last_name}".strip()


def _synthetic_emails(rec: ContractRecord):
    """Candidates in order: distinct names can share a slug ("Jean Marc", "Jean-Marc")."""
    slug = f"{rec.first_name}.{rec.last_name}".lower().replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c in ".-") or "unknown"
    yield f"{slug}@archive.invalid"
    n = 2
    while True:
        yield f"{slug}-{n}@archive.invalid"
        n += 1


def _group_people(records: list[ContractRecord]) -> list[list[ContractRecord]]:
    """One group per person: by email when given, since homonyms exist.

    A row without email joins the only emailed group of the same name; with
    none or several such groups it falls back to grouping by name.
    """
    by_email: dict[str, list[ContractRecord]] = {}
    for rec in (r for r in records if r.email):
        by_email.setdefault(rec.email, []).append(rec)
    emails_by_name: dict[tuple[str, str], set[str]] = {}
    for email, recs in by_email.items():
        for rec in recs:
            emails_by_name.setdefault(_person_key(rec), set()).add(email)
    by_name: dict[tuple[str, str], list[ContractRecord]] = {}
    for rec in (r for r in records if not r.email):
        emails = emails_by_name.get(_person_key(rec), set())
        if len(emails) == 1:
            by_email[next(iter(emails))].append(rec)
        else:
            by_name.setdefault(_person_key(rec), []).append(rec)
    return [*by_email.values(), *by_name.values()]


def _stored_employer(rec: ContractRecord) -> str:
    return rec.employer or "Unknown"


async def import_personnel(records: Iterable[ContractRecord]) -> ImportSummary:
    """Upsert one inactive User per person and one UserTenure per contract.

    Idempotent: people match by email; tenures already present
    (same start_date + status + employer) are skipped. A contract overlapping
    one already recorded is skipped and counted, as add_tenure would refuse it.
    """
    records = list(records)
    summary = ImportSummary(rows_total=len(records))

    async with session_scope() as session:
        for contracts in _group_people(records):
            user = await _resolve_user(session, contracts, summary)
            existing = await _existing_tenures(session, user.id)
            for rec in sorted(contracts, key=lambda r: r.start_date):
                if (rec.start_date, rec.status, _stored_employer(rec)) in {t[:3] for t in existing}:
                    summary.tenures_skipped_existing += 1
                    continue
                if any(periods_overlap(t[0], t[3], rec.start_date, rec.end_date) for t in existing):
                    summary.tenures_skipped_overlap += 1
                    continue
                session.add(
                    UserTenure(
                        user_id=user.id,
                        status=rec.status,
                        employer=_stored_employer(rec),
                        start_date=rec.start_date,
                        end_date=rec.end_date,
                        notes=rec.notes or None,
                    )
                )
                existing.append((rec.start_date, rec.status, _stored_employer(rec), rec.end_date))
                summary.tenures_created += 1
        await session.commit()

    return summary


async def _resolve_user(session, contracts: list[ContractRecord], summary: ImportSummary) -> User:
    latest = max(contracts, key=lambda r: r.start_date)
    given = next((c.email for c in contracts if c.email), "")
    found, email = (await _find_by_email(session, given), given) if given else await _find_archived(session, latest)
    if found is not None:
        summary.people_matched += 1
        return found

    user = User(
        email=email,
        hashed_password=NO_LOGIN_PASSWORD,
        is_active=False,
        auth_method=AuthMethod.LOCAL,
        role="",
        full_name=_full_name(latest),
        company=latest.employer or None,
        team=latest.team or None,
        employment_status=latest.status,
    )
    session.add(user)
    await session.flush()  # assign user.id before inserting child tenures
    summary.people_created += 1
    return user


async def _find_by_email(session, email: str) -> User | None:
    return (
        await session.execute(select(User).where(func.lower(User.email) == email.lower()))
    ).scalar_one_or_none()


async def _find_archived(session, latest: ContractRecord) -> tuple[User | None, str]:
    """The person a previous import created, or a free synthetic address."""
    for email in _synthetic_emails(latest):
        found = await _find_by_email(session, email)
        if found is None or found.full_name == _full_name(latest):
            return found, email


async def _existing_tenures(session, user_id) -> list[tuple]:
    """(start_date, status, employer, end_date) per recorded tenure."""
    rows = (
        await session.execute(select(UserTenure).where(UserTenure.user_id == user_id))
    ).scalars().all()
    return [(t.start_date, t.status, t.employer, t.end_date) for t in rows]
