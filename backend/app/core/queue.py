"""Redis job queue (arq) helpers shared by the API, the bot and the worker."""

import logging
import uuid

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

logger = logging.getLogger(__name__)

QUEUE_NAME = "taskbot:jobs"
NOTIFICATION_QUEUE_NAME = "taskbot:notifications"

TRANSCRIBE_JOB = "transcribe_voice"
NOTIFY_JOB = "notify_status_change"


def redis_settings(redis_url: str) -> RedisSettings:
    return RedisSettings.from_dsn(redis_url)


async def create_queue_pool(redis_url: str) -> ArqRedis:
    """An arq pool. It is a regular redis-py client too, so we also use it for pub/sub."""
    return await create_pool(redis_settings(redis_url), default_queue_name=QUEUE_NAME)


async def enqueue_transcription(pool: ArqRedis, job_id: uuid.UUID) -> bool:
    """Queue a transcription job. The arq job id equals our DB job id, so enqueueing the same
    job twice is a no-op. Returns False if it was already queued."""
    job = await pool.enqueue_job(
        TRANSCRIBE_JOB, str(job_id), _job_id=f"transcribe:{job_id}", _queue_name=QUEUE_NAME
    )
    return job is not None


async def enqueue_status_notification(pool: ArqRedis, task_id: int, status: str) -> None:
    await pool.enqueue_job(NOTIFY_JOB, task_id, status, _queue_name=NOTIFICATION_QUEUE_NAME)
