import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


TERMINAL_JOB_STATUSES = frozenset({JobStatus.SUCCEEDED.value, JobStatus.FAILED.value})


class TranscriptionJob(Base):
    """Durable record of one voice message going through the queue.

    Redis holds the in-flight job; this row is the source of truth for its outcome, which makes
    failures observable (and debuggable) after the queue entry is gone.
    """

    __tablename__ = "transcription_jobs"
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="uq_transcription_jobs_chat_message"),
        CheckConstraint("status IN ('queued', 'processing', 'succeeded', 'failed')", name="status_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)  # the user's voice message
    ack_message_id: Mapped[int | None] = mapped_column(BigInteger)  # the bot's "Transcribing…"
    file_id: Mapped[str] = mapped_column(Text)
    file_unique_id: Mapped[str | None] = mapped_column(String(128))
    audio_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(12), default=JobStatus.QUEUED.value, server_default=JobStatus.QUEUED.value, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16))
    processing_ms: Mapped[int | None] = mapped_column(Integer)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
