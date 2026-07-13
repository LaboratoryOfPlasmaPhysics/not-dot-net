"""Floor plan models — an uploaded plan image and the labeled pins on it."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base


class FloorPlan(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "floor_plan"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    image_path: Mapped[str] = mapped_column(String(500))
    width_px: Mapped[int] = mapped_column()
    height_px: Mapped[int] = mapped_column()
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)


class MapPoint(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "map_point"

    floor_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("floor_plan.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(30))  # "room" | "desk" | "wall_plug" | "asset" | "other"
    x: Mapped[int] = mapped_column()
    y: Mapped[int] = mapped_column()
    polygon: Mapped[list[list[int]] | None] = mapped_column(JSON, nullable=True, default=None)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resource.id", ondelete="SET NULL"), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)
