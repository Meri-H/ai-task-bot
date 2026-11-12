"""Run jobs through a *real* arq worker on real Redis: enqueue -> execute -> retry -> persist."""

import pytest
from arq import func
from arq.worker import Worker
from sqlalchemy import func as sa_func
from sqlalchemy import select

from app.bot.intake import TaskIntake
from app.core.queue import NOTIFICATION_QUEUE_NAME, NOTIFY_JOB, QUEUE_NAME, TRANSCRIBE_JOB, redis_settings
from app.models import JobStatus, Task, TranscriptionJob
from app.services.users import TelegramSender
from app.transcription import InvalidAudioError, TransientTranscriptionError
from app.worker.jobs import notify_status_change, transcribe_voice
from tests.fakes import FakeBot, ScriptedTranscriber

SENDER = TelegramSender(telegram_id=3, chat_id=300, first_name="Cy")


def _worker(settings, session_factory, redis, transcriber, bot, *, queue_name=QUEUE_NAME) -> Worker:
    return Worker(
        functions=[func(transcribe_voice, name=TRANSCRIBE_JOB), func(notify_status_change, name=NOTIFY_JOB)],
        redis_pool=redis,
        queue_name=queue_name,
        burst=True,
        poll_delay=0.05,
        max_tries=settings.job_max_tries,
        handle_signals=False,
        ctx={
            "settings": settings,
            "session_factory": session_factory,
            "redis": redis,
            "bot": bot,
            "transcriber": transcriber,
        },
    )


async def _enqueue_voice(session_factory, redis) -> TranscriptionJob:
    intake = TaskIntake(session_factory, redis)
    job, _ = await intake.register_voice_job(
        SENDER, message_id=1, ack_message_id=2, file_id="f", file_unique_id="u", duration=3
    )
    assert await intake.enqueue_voice_job(job.id)
    return job


async def _state(session_factory, job_id):
    async with session_factory() as session:
        job = await session.get(TranscriptionJob, job_id)
        tasks = (await session.execute(select(sa_func.count(Task.id)))).scalar_one()
    return job, tasks


@pytest.mark.timeout(30)
async def test_job_flows_through_redis_queue_to_a_task(settings, session_factory, redis):
    job = await _enqueue_voice(session_factory, redis)
    bot = FakeBot()
    await _worker(settings, session_factory, redis, ScriptedTranscriber("Renew passport"), bot).main()

    stored, tasks = await _state(session_factory, job.id)
    assert stored.status == JobStatus.SUCCEEDED and tasks == 1
    assert "Renew passport" in bot.edits[0][2]
    assert await redis.zcard(QUEUE_NAME) == 0  # queue drained


@pytest.mark.timeout(30)
async def test_transient_failure_is_retried_by_the_queue_until_it_works(settings, session_factory, redis):
    job = await _enqueue_voice(session_factory, redis)
    transcriber = ScriptedTranscriber(
        TransientTranscriptionError("503"), TransientTranscriptionError("503"), "Third time lucky"
    )
    await _worker(settings, session_factory, redis, transcriber, FakeBot()).main()

    stored, tasks = await _state(session_factory, job.id)
    assert transcriber.calls == 3
    assert stored.status == JobStatus.SUCCEEDED and stored.attempts == 3
    assert tasks == 1


@pytest.mark.timeout(30)
async def test_persistent_outage_ends_in_failed_job_and_user_message(settings, session_factory, redis):
    job = await _enqueue_voice(session_factory, redis)
    transcriber = ScriptedTranscriber(TransientTranscriptionError("down"))
    bot = FakeBot()
    await _worker(settings, session_factory, redis, transcriber, bot).main()

    stored, tasks = await _state(session_factory, job.id)
    assert transcriber.calls == settings.job_max_tries
    assert stored.status == JobStatus.FAILED and tasks == 0
    assert len(bot.edits) == 1 and "temporarily unavailable" in bot.edits[0][2]


@pytest.mark.timeout(30)
async def test_permanent_failure_is_not_retried_by_the_queue(settings, session_factory, redis):
    job = await _enqueue_voice(session_factory, redis)
    transcriber = ScriptedTranscriber(InvalidAudioError("garbage"))
    await _worker(settings, session_factory, redis, transcriber, FakeBot()).main()

    stored, _ = await _state(session_factory, job.id)
    assert transcriber.calls == 1 and stored.status == JobStatus.FAILED


@pytest.mark.timeout(30)
async def test_status_notification_job_runs_through_queue(settings, session_factory, redis):
    from app.core.queue import enqueue_status_notification

    task = await TaskIntake(session_factory, redis).add_text_task(SENDER, 50, "Ship it")
    await enqueue_status_notification(redis, task.id, "completed")
    bot = FakeBot()
    await _worker(
        settings, session_factory, redis, ScriptedTranscriber("x"), bot, queue_name=NOTIFICATION_QUEUE_NAME
    ).main()
    assert len(bot.sent) == 1 and "Ship it" in bot.sent[0][1]


def test_worker_settings_are_wired_to_the_producer_job_names(settings):
    from app.worker.settings import WorkerSettings

    names = {f.name for f in WorkerSettings.functions}
    assert names == {TRANSCRIBE_JOB, NOTIFY_JOB}
    assert WorkerSettings.queue_name == QUEUE_NAME
    assert WorkerSettings.redis_settings == redis_settings(settings.redis_url)
