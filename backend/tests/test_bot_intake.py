import json

from redis.exceptions import ConnectionError as RedisConnectionError

from app.bot.intake import TaskIntake
from app.core.queue import QUEUE_NAME
from app.models import JobStatus, TaskStatus, TranscriptionJob
from app.services.events import EVENTS_CHANNEL
from app.services.tasks import update_status
from app.services.users import TelegramSender

ANN = TelegramSender(telegram_id=1, chat_id=10, username="ann", first_name="Ann")
BOB = TelegramSender(telegram_id=2, chat_id=20, username="bob", first_name="Bob")


async def test_text_message_becomes_task_and_event(session_factory, redis):
    pubsub = redis.pubsub()
    await pubsub.subscribe(EVENTS_CHANNEL)
    await pubsub.get_message(timeout=1)

    task = await TaskIntake(session_factory, redis).add_text_task(ANN, 100, "Book flights")

    assert (task.title, task.source, task.status) == ("Book flights", "text", "pending")
    assert task.user is not None and task.user.telegram_id == 1
    event = json.loads((await pubsub.get_message(ignore_subscribe_messages=True, timeout=2))["data"])
    assert event["type"] == "task.created" and event["task"]["id"] == task.id
    await pubsub.aclose()


async def test_redelivered_telegram_update_does_not_duplicate_task(session_factory, redis):
    intake = TaskIntake(session_factory, redis)
    first = await intake.add_text_task(ANN, 100, "Book flights")
    again = await intake.add_text_task(ANN, 100, "Book flights")
    assert first.id == again.id


async def test_same_text_in_different_messages_makes_two_tasks(session_factory, redis):
    intake = TaskIntake(session_factory, redis)
    a = await intake.add_text_task(ANN, 100, "Same")
    b = await intake.add_text_task(ANN, 101, "Same")
    assert a.id != b.id


async def test_voice_job_is_registered_once_per_message(session_factory, redis):
    intake = TaskIntake(session_factory, redis)
    kwargs = dict(message_id=5, ack_message_id=6, file_id="f", file_unique_id="u", duration=3)
    job, created = await intake.register_voice_job(ANN, **kwargs)
    again, created_again = await intake.register_voice_job(ANN, **kwargs)
    assert created and not created_again and job.id == again.id
    assert job.status == JobStatus.QUEUED


async def test_enqueue_is_idempotent_per_job(session_factory, redis):
    intake = TaskIntake(session_factory, redis)
    job, _ = await intake.register_voice_job(
        ANN, message_id=5, ack_message_id=6, file_id="f", file_unique_id="u", duration=3
    )
    assert await intake.enqueue_voice_job(job.id) is True
    assert await intake.enqueue_voice_job(job.id) is True  # duplicate delivery: harmless
    assert await redis.zcard(QUEUE_NAME) == 1


async def test_enqueue_failure_marks_job_failed_and_reports_it(session_factory, redis):
    class BrokenRedis:
        async def enqueue_job(self, *args, **kwargs):
            raise RedisConnectionError("redis is down")

    intake = TaskIntake(session_factory, redis)
    job, _ = await intake.register_voice_job(
        ANN, message_id=5, ack_message_id=6, file_id="f", file_unique_id="u", duration=3
    )
    broken = TaskIntake(session_factory, BrokenRedis())  # type: ignore[arg-type]

    assert await broken.enqueue_voice_job(job.id) is False  # caller can tell the user immediately

    async with session_factory() as session:
        stored = await session.get(TranscriptionJob, job.id)
    assert stored.status == JobStatus.FAILED and "redis is down" in stored.error


async def test_open_tasks_are_per_user_and_exclude_completed(session_factory, redis):
    intake = TaskIntake(session_factory, redis)
    await intake.add_text_task(ANN, 1, "ann open")
    done = await intake.add_text_task(ANN, 2, "ann done")
    await intake.add_text_task(BOB, 3, "bob open")
    async with session_factory() as session:
        from app.services.tasks import get_task

        await update_status(session, await get_task(session, done.id), TaskStatus.COMPLETED)
        await session.commit()

    assert [t.title for t in await intake.open_tasks(ANN)] == ["ann open"]
    assert [t.title for t in await intake.open_tasks(BOB)] == ["bob open"]
