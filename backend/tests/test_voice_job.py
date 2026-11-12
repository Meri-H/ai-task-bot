"""The voice path: download -> transcribe -> task, and every way it can go wrong."""

import json

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import GetFile, SendMessage
from arq import Retry
from sqlalchemy import func, select

from app import messages
from app.bot.intake import TaskIntake
from app.models import JobStatus, Task, TranscriptionJob
from app.services.events import EVENTS_CHANNEL
from app.services.users import TelegramSender
from app.transcription import (
    InvalidAudioError,
    ProviderConfigError,
    TransientTranscriptionError,
)
from app.worker.jobs import notify_status_change, transcribe_voice
from tests.fakes import FakeBot, ScriptedTranscriber

SENDER = TelegramSender(telegram_id=11, chat_id=1100, username="ann", first_name="Ann")
ACK_ID = 77


@pytest.fixture
def make_ctx(settings, session_factory, redis):
    def _make(transcriber, bot=None, *, job_try=1):
        return {
            "settings": settings,
            "session_factory": session_factory,
            "redis": redis,
            "bot": bot if bot is not None else FakeBot(),
            "transcriber": transcriber,
            "job_try": job_try,
        }

    return _make


@pytest.fixture
async def job_id(session_factory, redis):
    job, created = await TaskIntake(session_factory, redis).register_voice_job(
        SENDER, message_id=500, ack_message_id=ACK_ID, file_id="file-abc", file_unique_id="u1", duration=4
    )
    assert created
    return str(job.id)


async def _job(session_factory, job_id) -> TranscriptionJob:
    async with session_factory() as session:
        job = await session.get(TranscriptionJob, job_id)
        assert job is not None
        return job


async def _task_count(session_factory) -> int:
    async with session_factory() as session:
        return (await session.execute(select(func.count(Task.id)))).scalar_one()


def _network_error() -> TelegramNetworkError:
    return TelegramNetworkError(method=GetFile(file_id="x"), message="connection reset")


# --- success ---------------------------------------------------------------------------------


async def test_success_creates_task_publishes_event_and_replaces_ack(
    make_ctx, session_factory, redis, job_id
):
    pubsub = redis.pubsub()
    await pubsub.subscribe(EVENTS_CHANNEL)
    await pubsub.get_message(timeout=1)
    bot = FakeBot()

    result = await transcribe_voice(make_ctx(ScriptedTranscriber("Call the dentist"), bot), job_id)

    assert result == "succeeded"
    async with session_factory() as session:
        task = (await session.execute(select(Task))).scalars().unique().one()
    assert (task.title, task.source, task.status) == ("Call the dentist", "voice", "pending")
    assert task.user is not None and task.user.telegram_id == 11

    job = await _job(session_factory, job_id)
    assert job.status == JobStatus.SUCCEEDED and job.task_id == task.id
    assert job.transcript == "Call the dentist" and job.language == "en"
    assert job.attempts == 1 and job.processing_ms is not None and job.finished_at is not None

    # The "Transcribing…" message itself is edited into the result (no extra chat noise).
    assert bot.edits == [(1100, ACK_ID, messages.voice_task_created(task.id, "Call the dentist"))]
    assert bot.sent == []

    event = json.loads((await pubsub.get_message(ignore_subscribe_messages=True, timeout=2))["data"])
    assert event["type"] == "task.created" and event["task"]["source"] == "voice"
    await pubsub.aclose()


async def test_long_transcript_keeps_full_text_in_description(make_ctx, session_factory, job_id):
    text = "Plan the quarterly review. " + "Cover everything in detail. " * 20
    await transcribe_voice(make_ctx(ScriptedTranscriber(text)), job_id)
    async with session_factory() as session:
        task = (await session.execute(select(Task))).scalars().unique().one()
    assert len(task.title) <= 200 and task.title.startswith("Plan the quarterly review.")
    assert task.description == text.strip()


# --- retries & failures ------------------------------------------------------------------------


async def test_transient_error_asks_queue_to_retry_with_backoff(make_ctx, session_factory, job_id, settings):
    bot = FakeBot()
    with pytest.raises(Retry) as info:
        await transcribe_voice(
            make_ctx(ScriptedTranscriber(TransientTranscriptionError("503")), bot, job_try=1), job_id
        )
    assert info.value.defer_score == int(settings.backoff_seconds(1) * 1000)

    job = await _job(session_factory, job_id)
    assert job.status == JobStatus.PROCESSING and job.attempts == 1  # not failed yet
    assert await _task_count(session_factory) == 0
    assert bot.edits == [] and bot.sent == []  # user keeps seeing "Transcribing…", no error noise


async def test_backoff_grows_exponentially(settings):
    delays = [settings.backoff_seconds(n) for n in (1, 2, 3)]
    assert delays == [delays[0], delays[0] * 2, delays[0] * 4]


async def test_retries_exhausted_marks_failed_and_tells_user(make_ctx, session_factory, job_id, settings):
    bot = FakeBot()
    ctx = make_ctx(
        ScriptedTranscriber(TransientTranscriptionError("still down")), bot, job_try=settings.job_max_tries
    )
    assert await transcribe_voice(ctx, job_id) == "failed"

    job = await _job(session_factory, job_id)
    assert job.status == JobStatus.FAILED and "still down" in job.error
    assert await _task_count(session_factory) == 0
    assert bot.edits == [(1100, ACK_ID, messages.VOICE_FAILURES["unavailable"])]


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (InvalidAudioError("corrupt"), "invalid_audio"),
        (ProviderConfigError("bad key"), "misconfigured"),
    ],
)
async def test_permanent_errors_fail_immediately_without_retry(
    make_ctx, session_factory, job_id, error, kind
):
    bot = FakeBot()
    result = await transcribe_voice(make_ctx(ScriptedTranscriber(error), bot, job_try=1), job_id)  # no Retry!
    assert result == "failed"
    assert (await _job(session_factory, job_id)).status == JobStatus.FAILED
    assert bot.edits[-1][2] == messages.VOICE_FAILURES[kind]
    assert await _task_count(session_factory) == 0


@pytest.mark.parametrize("silence", ["", "   \n "])
async def test_empty_transcript_is_a_friendly_failure_not_an_empty_task(
    make_ctx, session_factory, job_id, silence
):
    bot = FakeBot()
    assert await transcribe_voice(make_ctx(ScriptedTranscriber(silence), bot), job_id) == "failed"
    assert bot.edits[-1][2] == messages.VOICE_FAILURES["invalid_audio"]
    assert await _task_count(session_factory) == 0


async def test_unexpected_exception_is_retried_then_failed_gracefully(
    make_ctx, session_factory, job_id, settings
):
    transcriber = ScriptedTranscriber(RuntimeError("bug"))
    with pytest.raises(Retry):
        await transcribe_voice(make_ctx(transcriber, job_try=1), job_id)

    bot = FakeBot()
    ctx = make_ctx(transcriber, bot, job_try=settings.job_max_tries)
    assert await transcribe_voice(ctx, job_id) == "failed"
    assert bot.edits[-1][2] == messages.VOICE_FAILURES["unknown"]


async def test_transcription_timeout_is_transient(make_ctx, settings, session_factory, job_id):
    class Hangs(ScriptedTranscriber):
        async def transcribe(self, audio, filename="voice.ogg"):
            import asyncio

            await asyncio.sleep(10)

    ctx = make_ctx(Hangs())
    ctx["settings"] = settings.model_copy(update={"transcription_timeout_seconds": 0.05})
    with pytest.raises(Retry):
        await transcribe_voice(ctx, job_id)


async def test_retry_then_success_yields_exactly_one_task(make_ctx, session_factory, job_id):
    transcriber = ScriptedTranscriber(TransientTranscriptionError("blip"), "Water the plants")
    with pytest.raises(Retry):
        await transcribe_voice(make_ctx(transcriber, job_try=1), job_id)
    assert await transcribe_voice(make_ctx(transcriber, job_try=2), job_id) == "succeeded"

    assert await _task_count(session_factory) == 1
    job = await _job(session_factory, job_id)
    assert job.status == JobStatus.SUCCEEDED and job.attempts == 2


async def test_redelivered_job_after_success_is_a_noop(make_ctx, session_factory, job_id):
    bot = FakeBot()
    transcriber = ScriptedTranscriber("Once only")
    await transcribe_voice(make_ctx(transcriber, bot), job_id)
    assert await transcribe_voice(make_ctx(transcriber, bot), job_id) == "succeeded"

    assert await _task_count(session_factory) == 1
    assert transcriber.calls == 1 and len(bot.edits) == 1  # no second transcription, no second message


async def test_missing_job_is_dropped(make_ctx):
    assert (
        await transcribe_voice(make_ctx(ScriptedTranscriber("x")), "00000000-0000-0000-0000-000000000000")
        == "missing"
    )


# --- Telegram edge cases -----------------------------------------------------------------------


async def test_telegram_download_network_error_is_retried(make_ctx, job_id):
    bot = FakeBot(get_file_error=_network_error())
    with pytest.raises(Retry):
        await transcribe_voice(make_ctx(ScriptedTranscriber("x"), bot, job_try=1), job_id)


async def test_telegram_file_rejected_is_permanent(make_ctx, session_factory, job_id):
    bot = FakeBot(get_file_error=TelegramBadRequest(method=GetFile(file_id="x"), message="file is too big"))
    assert await transcribe_voice(make_ctx(ScriptedTranscriber("x"), bot), job_id) == "failed"
    assert bot.edits[-1][2] == messages.VOICE_FAILURES["invalid_audio"]


async def test_empty_download_is_invalid_audio(make_ctx, job_id):
    bot = FakeBot(audio=b"")
    assert await transcribe_voice(make_ctx(ScriptedTranscriber("x"), bot), job_id) == "failed"


async def test_task_survives_when_telegram_edit_fails(make_ctx, session_factory, job_id):
    """The DB write is the source of truth; a deleted/uneditable chat message must not undo it."""
    bot = FakeBot(
        edit_error=TelegramBadRequest(method=GetFile(file_id="x"), message="message to edit not found")
    )
    assert await transcribe_voice(make_ctx(ScriptedTranscriber("Pay rent"), bot), job_id) == "succeeded"
    assert await _task_count(session_factory) == 1
    assert len(bot.sent) == 1 and "Pay rent" in bot.sent[0][1]  # fell back to a fresh message


async def test_task_survives_when_telegram_is_entirely_unreachable(make_ctx, session_factory, job_id):
    err = _network_error()
    bot = FakeBot(edit_error=err, send_error=err)
    assert await transcribe_voice(make_ctx(ScriptedTranscriber("Pay rent"), bot), job_id) == "succeeded"
    assert await _task_count(session_factory) == 1


# --- status-change notifications ---------------------------------------------------------------


async def _make_task_for_sender(session_factory, redis) -> int:
    task = await TaskIntake(session_factory, redis).add_text_task(SENDER, 900, "Ship it")
    return task.id


async def test_notify_sends_status_to_author(make_ctx, session_factory, redis):
    task_id = await _make_task_for_sender(session_factory, redis)
    bot = FakeBot()
    assert await notify_status_change(make_ctx(None, bot), task_id, "completed") == "sent"
    assert bot.sent == [(1100, messages.status_changed(task_id, "Ship it", "completed"))]


async def test_notify_skips_unknown_task_and_missing_bot(make_ctx, session_factory, redis):
    task_id = await _make_task_for_sender(session_factory, redis)
    assert await notify_status_change(make_ctx(None), 9999, "completed") == "skipped"
    ctx = make_ctx(None)
    ctx["bot"] = None
    assert await notify_status_change(ctx, task_id, "completed") == "skipped"


async def test_notify_ignores_users_who_blocked_the_bot(make_ctx, session_factory, redis):
    task_id = await _make_task_for_sender(session_factory, redis)
    bot = FakeBot(
        send_error=TelegramForbiddenError(method=SendMessage(chat_id=1, text="x"), message="bot was blocked")
    )
    assert await notify_status_change(make_ctx(None, bot), task_id, "completed") == "blocked"


async def test_notify_retries_network_errors_then_gives_up(make_ctx, session_factory, redis, settings):
    task_id = await _make_task_for_sender(session_factory, redis)
    bot = FakeBot(send_error=_network_error())
    with pytest.raises(Retry):
        await notify_status_change(make_ctx(None, bot, job_try=1), task_id, "completed")
    assert (
        await notify_status_change(make_ctx(None, bot, job_try=settings.job_max_tries), task_id, "completed")
        == "failed"
    )
