"""Notifications must have capacity even when the CPU voice worker is occupied."""

import asyncio
import os
import uuid
from unittest.mock import AsyncMock

import pytest
from arq import func
from arq.worker import Worker

from app.core.queue import (
    NOTIFICATION_QUEUE_NAME,
    NOTIFY_JOB,
    QUEUE_NAME,
    TRANSCRIBE_JOB,
    create_queue_pool,
    enqueue_status_notification,
    enqueue_transcription,
)
from app.worker import settings as worker_settings
from tests.conftest import _reachable


async def test_producers_route_voice_and_notifications_to_independent_queues():
    pool = AsyncMock()
    job_id = uuid.uuid4()
    await enqueue_transcription(pool, job_id)
    await enqueue_status_notification(pool, 7, "in_progress")
    voice, notice = pool.enqueue_job.await_args_list
    assert voice.args == (TRANSCRIBE_JOB, str(job_id))
    assert voice.kwargs["_queue_name"] == QUEUE_NAME
    assert notice.args == (NOTIFY_JOB, 7, "in_progress")
    assert notice.kwargs["_queue_name"] == NOTIFICATION_QUEUE_NAME
    assert NOTIFICATION_QUEUE_NAME != QUEUE_NAME


def test_notification_worker_only_consumes_notifications():
    settings = worker_settings.NotificationWorkerSettings
    assert settings.queue_name == NOTIFICATION_QUEUE_NAME
    assert {f.name for f in settings.functions} == {NOTIFY_JOB}
    assert settings.on_startup is worker_settings.notification_startup
    assert settings.max_jobs >= 1


async def test_notification_startup_does_not_load_whisper(monkeypatch):
    resources = AsyncMock()
    monkeypatch.setattr(worker_settings, "startup_resources", resources)

    def unexpected_model_load(*args, **kwargs):
        raise AssertionError("Notification startup must not load a transcription model")

    monkeypatch.setattr(worker_settings, "build_transcriber", unexpected_model_load)
    ctx = {}
    await worker_settings.notification_startup(ctx)
    resources.assert_awaited_once_with(ctx)
    assert "transcriber" not in ctx


@pytest.mark.timeout(15)
async def test_real_redis_notification_finishes_while_voice_worker_is_blocked(settings):
    if not _reachable(settings.redis_url, 6379):
        if os.environ.get("REQUIRE_INTEGRATION_TESTS") == "true":
            pytest.fail("Redis required for notification isolation check")
        pytest.skip("Redis not reachable (set TEST_REDIS_URL)")

    pool = await create_queue_pool(settings.redis_url)
    voice_pool = await create_queue_pool(settings.redis_url)
    notification_pool = await create_queue_pool(settings.redis_url)
    await pool.flushdb()
    voice_started, release_voice, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def blocked_voice(ctx, job_id):
        voice_started.set()
        await release_voice.wait()

    async def deliver_notice(ctx, task_id, status):
        assert (task_id, status) == (7, "in_progress")
        delivered.set()

    voice_worker = Worker(
        functions=[func(blocked_voice, name=TRANSCRIBE_JOB)],
        redis_pool=voice_pool,
        queue_name=QUEUE_NAME,
        max_jobs=1,
        burst=True,
        poll_delay=0.01,
        handle_signals=False,
    )
    notification_worker = Worker(
        functions=[func(deliver_notice, name=NOTIFY_JOB)],
        redis_pool=notification_pool,
        queue_name=NOTIFICATION_QUEUE_NAME,
        max_jobs=1,
        burst=True,
        poll_delay=0.01,
        handle_signals=False,
    )
    await enqueue_transcription(pool, uuid.uuid4())
    voice_task = asyncio.create_task(voice_worker.main())
    try:
        await asyncio.wait_for(voice_started.wait(), timeout=5)
        await enqueue_status_notification(pool, 7, "in_progress")
        await asyncio.wait_for(notification_worker.main(), timeout=5)
        assert delivered.is_set()
        assert not voice_task.done()
        assert not release_voice.is_set()
        assert notification_worker.jobs_complete == 1
        assert notification_worker.jobs_failed == 0
    finally:
        release_voice.set()
        await asyncio.wait_for(voice_task, timeout=5)
        await voice_worker.close()
        await notification_worker.close()
        await pool.flushdb()
        await pool.aclose()
