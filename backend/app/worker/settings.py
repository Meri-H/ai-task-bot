"""arq worker configuration. Run with:  arq app.worker.settings.WorkerSettings"""

import logging
from typing import Any

from arq import func

from app.core.config import get_settings
from app.core.db import create_engine, create_session_factory
from app.core.logging import configure_logging
from app.core.queue import NOTIFICATION_QUEUE_NAME, NOTIFY_JOB, QUEUE_NAME, TRANSCRIBE_JOB, redis_settings
from app.telegram import build_bot
from app.transcription import build_transcriber
from app.worker.jobs import notify_status_change, transcribe_voice

logger = logging.getLogger(__name__)

_settings = get_settings()


async def startup_resources(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    engine = create_engine(settings.database_url, pool_size=10, max_overflow=10)
    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["session_factory"] = create_session_factory(engine)
    ctx["bot"] = build_bot(settings) if settings.telegram_bot_token else None
    if ctx["bot"] is None:
        logger.warning("TELEGRAM_BOT_TOKEN is not set: voice jobs will fail, notifications are off")


async def startup(ctx: dict[str, Any]) -> None:
    await startup_resources(ctx)
    settings = ctx["settings"]
    transcriber = build_transcriber(settings)
    ctx["transcriber"] = transcriber
    logger.info("transcription provider: %s", transcriber.name)
    try:
        # Load the model now, not on the first voice message: that first message is the one a
        # user (or reviewer) is watching, and a cold model load can take many seconds.
        await transcriber.warmup()
    except Exception:
        logger.exception("transcriber warmup failed; jobs will retry the load")


async def notification_startup(ctx: dict[str, Any]) -> None:
    # Notification delivery must not wait for a Whisper download, warmup, or voice backlog.
    await startup_resources(ctx)
    logger.info("notification worker ready; queue=%s", NOTIFICATION_QUEUE_NAME)


async def shutdown(ctx: dict[str, Any]) -> None:
    if ctx.get("bot") is not None:
        await ctx["bot"].session.close()
    transcriber = ctx.get("transcriber")
    if transcriber is not None and hasattr(transcriber, "aclose"):
        await transcriber.aclose()
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [
        # Registered under explicit names so they match the producers in app.core.queue.
        func(transcribe_voice, name=TRANSCRIBE_JOB),
        # Drain notifications queued by older versions without losing them during upgrade.
        func(notify_status_change, name=NOTIFY_JOB),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings(_settings.redis_url)
    queue_name = QUEUE_NAME
    max_jobs = _settings.worker_concurrency
    max_tries = _settings.job_max_tries
    job_timeout = _settings.job_timeout_seconds
    keep_result = 3600
    health_check_interval = 30


class NotificationWorkerSettings:
    functions = [func(notify_status_change, name=NOTIFY_JOB)]
    on_startup = notification_startup
    on_shutdown = shutdown
    redis_settings = redis_settings(_settings.redis_url)
    queue_name = NOTIFICATION_QUEUE_NAME
    max_jobs = _settings.notification_worker_concurrency
    max_tries = _settings.job_max_tries
    job_timeout = _settings.job_timeout_seconds
    poll_delay = 0.1
    keep_result = 3600
    health_check_interval = 30
