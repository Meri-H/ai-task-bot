"""Framework-independent bot logic: everything the handlers do that touches the DB or the queue.

Keeping this free of aiogram types makes it testable without constructing Telegram objects, and
keeps the handlers down to "parse the update, call intake, reply".
"""

import logging
import uuid

from arq.connections import ArqRedis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.queue import enqueue_transcription
from app.models import Task, TaskSource, TaskStatus, TranscriptionJob, User
from app.services import jobs as job_service
from app.services.events import TASK_CREATED, publish_task_event
from app.services.tasks import create_task, split_title_description
from app.services.users import TelegramSender, upsert_user

logger = logging.getLogger(__name__)


class TaskIntake:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], redis: ArqRedis) -> None:
        self._sf = session_factory
        self._redis = redis

    async def add_text_task(self, sender: TelegramSender, message_id: int, text: str) -> Task:
        title, description = split_title_description(text)
        async with self._sf() as session:
            user = await upsert_user(session, sender)
            task, created = await create_task(
                session,
                title=title,
                description=description,
                source=TaskSource.TEXT,
                user_id=user.id,
                idempotency_key=f"tg:{sender.chat_id}:{message_id}",
            )
            await session.commit()
        if created:
            await publish_task_event(self._redis, TASK_CREATED, task)
        return task

    async def register_voice_job(
        self,
        sender: TelegramSender,
        *,
        message_id: int,
        ack_message_id: int,
        file_id: str,
        file_unique_id: str | None,
        duration: int | None,
    ) -> tuple[TranscriptionJob, bool]:
        """Persist the job *before* queueing it, so there is always a durable record to update
        even if the worker is down or the queue is unreachable."""
        async with self._sf() as session:
            user = await upsert_user(session, sender)
            job, created = await job_service.create_job(
                session,
                user_id=user.id,
                chat_id=sender.chat_id,
                message_id=message_id,
                ack_message_id=ack_message_id,
                file_id=file_id,
                file_unique_id=file_unique_id,
                audio_seconds=duration,
            )
            await session.commit()
        return job, created

    async def enqueue_voice_job(self, job_id: uuid.UUID) -> bool:
        """Queue the job. If Redis is unreachable, record the failure and return False so the
        caller can tell the user right away instead of leaving them on "Transcribing…"."""
        try:
            await enqueue_transcription(self._redis, job_id)
        except (RedisError, OSError) as exc:
            logger.error("could not enqueue job=%s: %s", job_id, exc)
            await self.fail_job(job_id, f"enqueue failed: {exc}")
            return False
        return True

    async def fail_job(self, job_id: uuid.UUID, error: str) -> None:
        async with self._sf() as session:
            job = await job_service.get_job(session, job_id)
            if job is not None:
                await job_service.mark_failed(session, job, error)
                await session.commit()

    async def open_tasks(self, sender: TelegramSender, limit: int = 10) -> list[Task]:
        async with self._sf() as session:
            rows = (
                (
                    await session.execute(
                        select(Task)
                        .join(User, Task.user_id == User.id)
                        .where(User.telegram_id == sender.telegram_id)
                        .where(Task.status != TaskStatus.COMPLETED.value)
                        .order_by(Task.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .unique()
            )
            return list(rows)
