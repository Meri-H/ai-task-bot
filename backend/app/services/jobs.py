import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobStatus, TranscriptionJob
from app.models.base import utcnow


async def create_job(
    session: AsyncSession,
    *,
    user_id: int | None,
    chat_id: int,
    message_id: int,
    ack_message_id: int | None,
    file_id: str,
    file_unique_id: str | None,
    audio_seconds: int | None,
) -> tuple[TranscriptionJob, bool]:
    """Insert a job row. (chat_id, message_id) is unique, so a re-delivered Telegram update
    resolves to the existing job instead of creating a second one."""
    stmt = (
        pg_insert(TranscriptionJob)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            ack_message_id=ack_message_id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            audio_seconds=audio_seconds,
            status=JobStatus.QUEUED.value,
        )
        .on_conflict_do_nothing(constraint="uq_transcription_jobs_chat_message")
        .returning(TranscriptionJob.id)
    )
    new_id = (await session.execute(stmt)).scalar_one_or_none()
    if new_id is not None:
        job = await session.get(TranscriptionJob, new_id, populate_existing=True)
        assert job is not None
        return job, True
    existing = (
        await session.execute(
            select(TranscriptionJob).where(
                TranscriptionJob.chat_id == chat_id, TranscriptionJob.message_id == message_id
            ),
            execution_options={"populate_existing": True},
        )
    ).scalar_one()
    return existing, False


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> TranscriptionJob | None:
    return await session.get(TranscriptionJob, job_id, populate_existing=True)


async def mark_processing(session: AsyncSession, job: TranscriptionJob, attempt: int) -> None:
    job.status = JobStatus.PROCESSING.value
    job.attempts = attempt
    job.updated_at = utcnow()
    await session.flush()


async def mark_succeeded(
    session: AsyncSession,
    job: TranscriptionJob,
    *,
    task_id: int,
    transcript: str,
    language: str | None,
    processing_ms: int,
) -> None:
    now = utcnow()
    job.status = JobStatus.SUCCEEDED.value
    job.task_id = task_id
    job.transcript = transcript
    job.language = language
    job.processing_ms = processing_ms
    job.error = None
    job.updated_at = now
    job.finished_at = now
    await session.flush()


async def mark_failed(session: AsyncSession, job: TranscriptionJob, error: str) -> None:
    now = utcnow()
    job.status = JobStatus.FAILED.value
    job.error = error[:2000]
    job.updated_at = now
    job.finished_at = now
    await session.flush()
