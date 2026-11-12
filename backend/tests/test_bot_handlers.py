"""Voice acknowledgements must always reflect the recorded outcome on redelivery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import messages
from app.bot.handlers import on_voice
from app.core.config import Settings


def _message():
    ack = SimpleNamespace(message_id=42, edit_text=AsyncMock())
    message = SimpleNamespace(
        message_id=5,
        from_user=SimpleNamespace(id=7, username="ann", first_name="Ann"),
        chat=SimpleNamespace(id=7),
        voice=SimpleNamespace(duration=3, file_size=100, file_id="f", file_unique_id="u"),
        reply=AsyncMock(return_value=ack),
    )
    return message, ack


@pytest.mark.parametrize(
    ("status", "task_id", "expected"),
    [
        ("queued", None, "Check my original reply"),
        ("processing", None, "Check my original reply"),
        ("succeeded", 10, "already added as task #10"),
        ("succeeded", None, "task has since been removed"),
        ("failed", None, "previously failed"),
    ],
)
async def test_duplicate_voice_ack_is_resolved_without_requeueing(status, task_id, expected):
    message, ack = _message()
    job = SimpleNamespace(id="job-1", status=status, task_id=task_id)
    intake = SimpleNamespace(
        register_voice_job=AsyncMock(return_value=(job, False)), enqueue_voice_job=AsyncMock()
    )
    await on_voice(message, intake, Settings())
    assert expected in ack.edit_text.await_args.args[0]
    assert ack.edit_text.await_args.args[0] != messages.TRANSCRIBING
    intake.enqueue_voice_job.assert_not_awaited()


async def test_new_voice_is_acknowledged_and_queued():
    message, ack = _message()
    intake = SimpleNamespace(
        register_voice_job=AsyncMock(return_value=(SimpleNamespace(id="job-1"), True)),
        enqueue_voice_job=AsyncMock(return_value=True),
    )
    await on_voice(message, intake, Settings())
    message.reply.assert_awaited_once_with(messages.TRANSCRIBING)
    assert intake.register_voice_job.await_args.kwargs["ack_message_id"] == ack.message_id
    intake.enqueue_voice_job.assert_awaited_once_with("job-1")
    ack.edit_text.assert_not_awaited()


async def test_queue_failure_resolves_the_acknowledgement():
    message, ack = _message()
    intake = SimpleNamespace(
        register_voice_job=AsyncMock(return_value=(SimpleNamespace(id="job-1"), True)),
        enqueue_voice_job=AsyncMock(return_value=False),
    )
    await on_voice(message, intake, Settings())
    ack.edit_text.assert_awaited_once_with(messages.QUEUE_UNAVAILABLE)
