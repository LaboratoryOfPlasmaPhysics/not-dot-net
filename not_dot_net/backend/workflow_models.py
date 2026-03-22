import uuid
from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base


class WorkflowRequest(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "workflow_request"

    type: Mapped[str] = mapped_column(String(100))
    current_step: Mapped[str] = mapped_column(String(100))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(50), default="in_progress")
    data: Mapped[dict] = mapped_column(JSON, default_factory=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, default=None
    )
    target_email: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    token: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    token_expires_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), default=None
    )


class WorkflowEvent(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "workflow_event"

    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_request.id", ondelete="CASCADE")
    )
    step_key: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, default=None
    )
    actor_token: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    data_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)


class WorkflowFile(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "workflow_file"

    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_request.id", ondelete="CASCADE")
    )
    step_key: Mapped[str] = mapped_column(String(100))
    field_name: Mapped[str] = mapped_column(String(100))
    filename: Mapped[str] = mapped_column(String(500))
    storage_path: Mapped[str] = mapped_column(String(1000))
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, default=None
    )
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)
