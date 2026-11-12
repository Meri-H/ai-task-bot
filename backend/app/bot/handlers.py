import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import ErrorEvent, Message

from app import messages
from app.bot.intake import TaskIntake
from app.core.config import Settings
from app.services.users import TelegramSender

logger = logging.getLogger(__name__)

router = Router(name="tasks")

MAX_TEXT_CHARS = 4000


def _sender(message: Message) -> TelegramSender | None:
    user = message.from_user
    if user is None:
        return None
    return TelegramSender(
        telegram_id=user.id,
        chat_id=message.chat.id,
        username=user.username,
        first_name=user.first_name,
    )


@router.message(CommandStart())
async def on_start(message: Message, settings: Settings) -> None:
    await message.answer(messages.welcome(settings.dashboard_url))


@router.message(Command("help"))
async def on_help(message: Message) -> None:
    await message.answer(messages.HELP)


@router.message(Command("tasks"))
async def on_tasks(message: Message, intake: TaskIntake) -> None:
    sender = _sender(message)
    if sender is None:
        return
    tasks = await intake.open_tasks(sender)
    if not tasks:
        await message.answer("You have no open tasks. 🎉")
        return
    lines = [f"#{t.id} {messages.STATUS_LABELS.get(t.status, t.status)} – {t.title}" for t in tasks]
    await message.answer("Your open tasks:\n" + "\n".join(lines))


@router.message(F.text, ~F.text.startswith("/"))
async def on_text(message: Message, intake: TaskIntake) -> None:
    sender = _sender(message)
    text = (message.text or "").strip()
    if sender is None:
        return
    if not text:
        await message.reply(messages.EMPTY_TEXT)
        return
    task = await intake.add_text_task(sender, message.message_id, text[:MAX_TEXT_CHARS])
    await message.reply(messages.task_created(task.id, task.title))


@router.message(F.voice)
async def on_voice(message: Message, intake: TaskIntake, settings: Settings) -> None:
    sender = _sender(message)
    voice = message.voice
    if sender is None or voice is None:
        return

    # Cheap validation first: reject before spending any queue/transcription budget.
    if voice.duration > settings.max_voice_seconds:
        await message.reply(messages.voice_too_long(settings.max_voice_seconds))
        return
    if voice.file_size and voice.file_size > settings.max_voice_bytes:
        await message.reply(messages.voice_too_big(settings.max_voice_bytes // (1024 * 1024)))
        return

    # UX: acknowledge immediately. Everything slow happens in the worker, which later edits this
    # same message into the result.
    ack = await message.reply(messages.TRANSCRIBING)

    job, created = await intake.register_voice_job(
        sender,
        message_id=message.message_id,
        ack_message_id=ack.message_id,
        file_id=voice.file_id,
        file_unique_id=voice.file_unique_id,
        duration=voice.duration,
    )
    if not created:
        logger.info("duplicate voice update for message %s; status=%s", message.message_id, job.status)
        # Do not leave the new acknowledgement claiming transcription has just started.
        try:
            await ack.edit_text(messages.duplicate_voice(job.status, job.task_id))
        except TelegramAPIError:
            logger.warning("could not update duplicate acknowledgement for message=%s", message.message_id)
        return

    if not await intake.enqueue_voice_job(job.id):
        try:
            await ack.edit_text(messages.QUEUE_UNAVAILABLE)
        except TelegramAPIError:
            logger.warning("could not edit acknowledgement for job=%s", job.id)


@router.message(F.voice.is_(None) & ~F.text)
@router.message(F.text.startswith("/"))
async def on_unsupported(message: Message) -> None:
    await message.answer(messages.UNSUPPORTED)


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    """Last line of defence: log it, tell the user, keep polling."""
    logger.error("unhandled error while processing update", exc_info=event.exception)
    message = event.update.message
    if message is not None:
        try:
            await message.answer(messages.GENERIC_ERROR)
        except TelegramAPIError:
            pass
    return True
