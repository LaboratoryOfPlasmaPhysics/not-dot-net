"""User tenure tracking — employment periods with status and employer."""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, String, func, or_, select
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base, session_scope


class UserTenure(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "user_tenure"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(100))
    employer: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True, default=None, index=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)


def _validate_tenure_dates(start_date: date, end_date: date | None) -> None:
    if end_date is not None and end_date < start_date:
        raise ValueError("Tenure end date cannot be before start date")


async def _ensure_no_overlap(
    session,
    user_id: uuid.UUID,
    start_date: date,
    end_date: date | None,
    exclude_id: uuid.UUID | None = None,
) -> None:
    new_end = end_date or date.max
    result = await session.execute(
        select(UserTenure).where(UserTenure.user_id == user_id)
    )
    for existing in result.scalars().all():
        if exclude_id is not None and existing.id == exclude_id:
            continue
        existing_end = existing.end_date or date.max
        if existing.start_date <= new_end and start_date <= existing_end:
            raise ValueError("Tenure periods cannot overlap")


async def add_tenure(
    user_id: uuid.UUID,
    status: str,
    employer: str,
    start_date: date,
    end_date: date | None = None,
    notes: str | None = None,
) -> UserTenure:
    _validate_tenure_dates(start_date, end_date)
    async with session_scope() as session:
        await _ensure_no_overlap(session, user_id, start_date, end_date)
        tenure = UserTenure(
            user_id=user_id,
            status=status,
            employer=employer,
            start_date=start_date,
            end_date=end_date,
            notes=notes,
        )
        session.add(tenure)
        await session.commit()
        await session.refresh(tenure)
        return tenure


async def close_tenure(tenure_id: uuid.UUID, end_date: date) -> UserTenure:
    async with session_scope() as session:
        tenure = await session.get(UserTenure, tenure_id)
        if tenure is None:
            raise ValueError(f"Tenure {tenure_id} not found")
        _validate_tenure_dates(tenure.start_date, end_date)
        await _ensure_no_overlap(
            session, tenure.user_id, tenure.start_date, end_date, exclude_id=tenure.id,
        )
        tenure.end_date = end_date
        await session.commit()
        await session.refresh(tenure)
        return tenure


async def list_tenures(user_id: uuid.UUID) -> list[UserTenure]:
    async with session_scope() as session:
        result = await session.execute(
            select(UserTenure)
            .where(UserTenure.user_id == user_id)
            .order_by(UserTenure.start_date.asc())
        )
        return list(result.scalars().all())


async def current_tenure(user_id: uuid.UUID) -> UserTenure | None:
    async with session_scope() as session:
        result = await session.execute(
            select(UserTenure)
            .where(UserTenure.user_id == user_id, UserTenure.end_date == None)  # noqa: E711
            .order_by(UserTenure.start_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def avg_duration_by_status() -> dict[str, dict]:
    """Average tenure duration per status (only closed tenures)."""
    async with session_scope() as session:
        result = await session.execute(
            select(UserTenure).where(UserTenure.end_date != None)  # noqa: E711
        )
        tenures = result.scalars().all()

    by_status: dict[str, list[int]] = {}
    for t in tenures:
        days = (t.end_date - t.start_date).days
        by_status.setdefault(t.status, []).append(days)

    return {
        status: {
            "count": len(durations),
            "avg_days": round(sum(durations) / len(durations), 1),
        }
        for status, durations in by_status.items()
    }


async def headcount_at_date(target: date) -> int:
    """Count people with an active tenure on a given date."""
    async with session_scope() as session:
        result = await session.execute(
            select(func.count(func.distinct(UserTenure.user_id)))
            .where(
                UserTenure.start_date <= target,
                or_(
                    UserTenure.end_date == None,  # noqa: E711
                    UserTenure.end_date >= target,
                ),
            )
        )
        return result.scalar_one()


async def update_tenure(
    tenure_id: uuid.UUID,
    status: str | None = None,
    employer: str | None = None,
    start_date: date | None = None,
    end_date: date | None = ...,
    notes: str | None = ...,
) -> UserTenure:
    async with session_scope() as session:
        tenure = await session.get(UserTenure, tenure_id)
        if tenure is None:
            raise ValueError(f"Tenure {tenure_id} not found")
        new_start_date = start_date if start_date is not None else tenure.start_date
        new_end_date = end_date if end_date is not ... else tenure.end_date
        _validate_tenure_dates(new_start_date, new_end_date)
        await _ensure_no_overlap(
            session, tenure.user_id, new_start_date, new_end_date, exclude_id=tenure.id,
        )
        if status is not None:
            tenure.status = status
        if employer is not None:
            tenure.employer = employer
        if start_date is not None:
            tenure.start_date = start_date
        if end_date is not ...:
            tenure.end_date = end_date
        if notes is not ...:
            tenure.notes = notes
        await session.commit()
        await session.refresh(tenure)
        return tenure


async def delete_tenure(tenure_id: uuid.UUID) -> None:
    async with session_scope() as session:
        tenure = await session.get(UserTenure, tenure_id)
        if tenure is None:
            raise ValueError(f"Tenure {tenure_id} not found")
        await session.delete(tenure)
        await session.commit()
