"""Office availability — owner-declared booking windows for office resources.

Kept separate from booking_service.py: this module owns windows and
ownership; booking_service.py owns reservations and conflicts. The only
integration point is create_booking's is_covered() check.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, func
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base


class OfficeAvailability(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "office_availability"

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource.id", ondelete="CASCADE"), index=True
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    offered_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE")
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)


def is_covered(windows: list[OfficeAvailability], start_date: date, end_date: date) -> bool:
    """True if [start_date, end_date) is fully covered by the union of the
    given windows. Pure/no DB — overlapping/duplicate windows are absorbed
    for free by this sweep."""
    day = start_date
    for window in sorted(windows, key=lambda w: w.start_date):
        if window.start_date > day:
            break
        if window.end_date > day:
            day = window.end_date
    return day >= end_date
