from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.user import User


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TaskSource(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    WEB = "web"


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'in_progress', 'completed')", name="status_valid"),
        CheckConstraint("source IN ('text', 'voice', 'web')", name="source_valid"),
        Index("ix_tasks_status_created_at", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default=TaskStatus.PENDING.value, server_default=TaskStatus.PENDING.value
    )
    source: Mapped[str] = mapped_column(String(10))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    # Makes creation idempotent, e.g. "tg:<chat_id>:<message_id>" so that a retried queue job or a
    # re-delivered Telegram update can never create a duplicate task.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Many-to-one and tiny, so a joined load is cheap and avoids lazy loads under asyncio.
    user: Mapped[User | None] = relationship(lazy="joined")
