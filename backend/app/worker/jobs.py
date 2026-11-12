"""Queue jobs executed by the arq worker.

`transcribe_voice` is the interesting one. Its contract:

* The bot has already replied "🎙 Transcribing…" and stored a `TranscriptionJob` row.
* On success: create the task (idempotently), publish a real-time event, and turn the bot's
  acknowledgement into the result, all within one job.
* Transient failure (network, 429, 5xx, timeout): re-raise `arq.Retry` with exponential backoff
  until `job_max_tries` is exhausted.
* Permanent failure (corrupt/empty audio, bad credentials) or retries exhausted: mark the job
  failed, tell the user why, and create no task.
* Re-running a job that already finished is a no-op, so queue redelivery cannot duplicate tasks.
"""

import asyncio
import io
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from arq import Retry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import messages
from app.core.config import Settings
from app.models import TERMINAL_JOB_STATUSES, Task, TaskSource
from app.services import jobs as job_service
from app.services.events import TASK_CREATED, publish_task_event
from app.services.tasks import create_task, split_title_description
from app.transcription import (
    EmptyTranscriptError,
    InvalidAudioError,
    ProviderConfigError,
    Transcriber,
    TranscriptionError,
    TransientTranscriptionError,
)

logger = logging.getLogger(__name__)

Ctx = dict[str, Any]


@dataclass(frozen=True, slots=True)
class _JobSnapshot:
    """Plain values copied out of the DB row so we don't hold a session during slow I/O."""

    id: uuid.UUID
    user_id: int | None
    chat_id: int
    message_id: int
    ack_message_id: int | None
    file_id: str


# ----------------------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------------------


async def _download_voice(bot: Bot, file_id: str, settings: Settings) -> bytes:
    """Fetch the voice file from Telegram, mapping failures to our error taxonomy."""
    try:
        async with asyncio.timeout(settings.download_timeout_seconds):
            file = await bot.get_file(file_id)
            if not file.file_path:
                raise InvalidAudioError("Telegram returned no file path")
            buffer = io.BytesIO()
            await bot.download_file(file.file_path, destination=buffer)
    except TimeoutError as exc:
        raise TransientTranscriptionError("Telegram download timed out") from exc
    except TelegramEntityTooLarge as exc:  # subclass of TelegramNetworkError, but permanent
        raise InvalidAudioError("voice file is too large for Telegram bots to download") from exc
    except (TelegramNetworkError, TelegramServerError, TelegramRetryAfter) as exc:
        raise TransientTranscriptionError(f"Telegram download failed: {exc}") from exc
    except TelegramAPIError as exc:  # bad request / not found / forbidden: retrying won't help
        raise InvalidAudioError(f"Telegram refused the file: {exc}") from exc

    audio = buffer.getvalue()
    if not audio:
        raise InvalidAudioError("downloaded voice file is empty")
    if len(audio) > settings.max_voice_bytes:
        raise InvalidAudioError("voice file is too large")
    return audio


def _failure_kind(exc: BaseException) -> str:
    if isinstance(exc, InvalidAudioError):
        return "invalid_audio"
    if isinstance(exc, ProviderConfigError):
        return "misconfigured"
    if isinstance(exc, TransientTranscriptionError):
        return "unavailable"
    return "unknown"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, TranscriptionError):
        return isinstance(exc, TransientTranscriptionError)
    # Anything unexpected (a bug, a dropped DB connection…) gets the benefit of the doubt.
    return True


async def _edit_or_send(bot: Bot | None, chat_id: int, message_id: int | None, text: str) -> None:
    """Show `text` in place of the acknowledgement; fall back to a new message. Never raises:
    the user-visible outcome is already persisted, this is only the courtesy notification."""
    if bot is None:
        return
    try:
        if message_id is not None:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
            return
    except TelegramAPIError as exc:
        logger.warning("could not edit message %s in chat %s: %s", message_id, chat_id, exc)
    try:
        await bot.send_message(chat_id, text)
    except TelegramAPIError as exc:
        logger.warning("could not message chat %s: %s", chat_id, exc)


async def _fail_job(ctx: Ctx, snap: _JobSnapshot, exc: BaseException, attempt: int) -> str:
    sf: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    kind = _failure_kind(exc)
    logger.error(
        "job=%s failed permanently after %s attempt(s) kind=%s error=%s",
        snap.id,
        attempt,
        kind,
        exc,
        exc_info=not isinstance(exc, TranscriptionError),
    )
    async with sf() as session:
        job = await job_service.get_job(session, snap.id)
        if job is not None:
            await job_service.mark_failed(session, job, f"{type(exc).__name__}: {exc}")
            await session.commit()
    await _edit_or_send(ctx.get("bot"), snap.chat_id, snap.ack_message_id, messages.VOICE_FAILURES[kind])
    return "failed"


# ----------------------------------------------------------------------------------------------
# Jobs
# ----------------------------------------------------------------------------------------------


async def transcribe_voice(ctx: Ctx, job_id: str) -> str:
    settings: Settings = ctx["settings"]
    sf: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    transcriber: Transcriber = ctx["transcriber"]
    bot: Bot | None = ctx.get("bot")
    attempt: int = ctx.get("job_try", 1)
    jid = uuid.UUID(job_id)

    async with sf() as session:
        job = await job_service.get_job(session, jid)
        if job is None:
            logger.error("job=%s not found; dropping", job_id)
            return "missing"
        if job.status in TERMINAL_JOB_STATUSES:
            logger.info("job=%s already %s; nothing to do", job_id, job.status)
            return job.status
        await job_service.mark_processing(session, job, attempt)
        await session.commit()
        queue_wait_ms = int((time.time() - job.created_at.timestamp()) * 1000)
        snap = _JobSnapshot(
            id=job.id,
            user_id=job.user_id,
            chat_id=job.chat_id,
            message_id=job.message_id,
            ack_message_id=job.ack_message_id,
            file_id=job.file_id,
        )

    started = time.perf_counter()
    try:
        if bot is None:
            raise ProviderConfigError("TELEGRAM_BOT_TOKEN is not configured for the worker")
        audio = await _download_voice(bot, snap.file_id, settings)
        downloaded = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                transcriber.transcribe(audio), timeout=settings.transcription_timeout_seconds
            )
        except TimeoutError as exc:
            raise TransientTranscriptionError("transcription timed out") from exc
        text = result.text.strip()
        if not text:
            raise EmptyTranscriptError("no speech detected")
    except Exception as exc:
        if _is_retryable(exc) and attempt < settings.job_max_tries:
            delay = settings.backoff_seconds(attempt)
            logger.warning(
                "job=%s attempt %s/%s failed (%s: %s); retrying in %.1fs",
                snap.id,
                attempt,
                settings.job_max_tries,
                type(exc).__name__,
                exc,
                delay,
            )
            raise Retry(defer=delay) from exc
        return await _fail_job(ctx, snap, exc, attempt)

    processing_ms = int((time.perf_counter() - started) * 1000)
    title, description = split_title_description(text)
    async with sf() as session:
        job = await job_service.get_job(session, snap.id)
        assert job is not None
        task, created = await create_task(
            session,
            title=title,
            description=description,
            source=TaskSource.VOICE,
            user_id=snap.user_id,
            idempotency_key=f"tg:{snap.chat_id}:{snap.message_id}",
        )
        await job_service.mark_succeeded(
            session,
            job,
            task_id=task.id,
            transcript=text,
            language=result.language,
            processing_ms=processing_ms,
        )
        await session.commit()

    if created:
        await publish_task_event(ctx["redis"], TASK_CREATED, task)
    await _edit_or_send(bot, snap.chat_id, snap.ack_message_id, messages.voice_task_created(task.id, text))
    logger.info(
        "job=%s done task=%s lang=%s attempt=%s queue_wait_ms=%s download_ms=%s transcribe_ms=%s total_ms=%s",
        snap.id,
        task.id,
        result.language,
        attempt,
        queue_wait_ms,
        int((downloaded - started) * 1000),
        int((time.perf_counter() - downloaded) * 1000),
        processing_ms,
    )
    return "succeeded"


async def notify_status_change(ctx: Ctx, task_id: int, status: str) -> str:
    """Tell the task's Telegram author that its status changed on the dashboard."""
    settings: Settings = ctx["settings"]
    sf: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    bot: Bot | None = ctx.get("bot")
    attempt: int = ctx.get("job_try", 1)

    if bot is None:
        return "skipped"
    async with sf() as session:
        task = (
            (await session.execute(select(Task).where(Task.id == task_id))).scalars().unique().one_or_none()
        )
    if task is None or task.user is None:
        return "skipped"

    try:
        await bot.send_message(task.user.chat_id, messages.status_changed(task.id, task.title, status))
    except TelegramForbiddenError:
        logger.info("user %s blocked the bot; dropping notification", task.user.id)
        return "blocked"
    except TelegramRetryAfter as exc:
        if attempt < settings.job_max_tries:
            raise Retry(defer=exc.retry_after) from exc
        logger.warning("giving up notifying task=%s: rate limited", task_id)
        return "failed"
    except (TelegramNetworkError, TelegramServerError) as exc:
        if attempt < settings.job_max_tries:
            raise Retry(defer=settings.backoff_seconds(attempt)) from exc
        logger.warning("giving up notifying task=%s: %s", task_id, exc)
        return "failed"
    except TelegramAPIError as exc:
        logger.warning("notification for task=%s rejected: %s", task_id, exc)
        return "failed"
    return "sent"
