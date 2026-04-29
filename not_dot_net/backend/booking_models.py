"""Booking system models — resources and reservations."""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base


class Resource(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "resource"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    resource_type: Mapped[str] = mapped_column(String(50))  # desktop, laptop
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)
    specs: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)  # {cpu, ram, hdd, gpu}
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)


class Booking(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "booking"

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    os_choice: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)
    software_tags: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    note: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)
