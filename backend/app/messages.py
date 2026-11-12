"""User-facing bot texts (plain text on purpose: no markup, so user content can't break it)."""

from app.models import TaskStatus

TRANSCRIBING = "🎙 Transcribing…"

STATUS_LABELS = {
    TaskStatus.PENDING.value: "⏳ Pending",
    TaskStatus.IN_PROGRESS.value: "🔧 In progress",
    TaskStatus.COMPLETED.value: "✅ Completed",
}

HELP = (
    "Send me a text message or a voice note and I'll add it as a task.\n\n"
    "/tasks – your open tasks\n"
    "/help – this message"
)

QUEUE_UNAVAILABLE = "⚠️ I couldn't queue your voice message right now. Please try again in a moment."
GENERIC_ERROR = "⚠️ Something went wrong on my side. Please try again."
UNSUPPORTED = "I can turn text and voice messages into tasks. Try one of those! (/help)"
EMPTY_TEXT = "That message was empty — send some text or a voice note."


def duplicate_voice(status: str, task_id: int | None) -> str:
    if status == "succeeded":
        if task_id is not None:
            return f"✅ This voice note was already added as task #{task_id}."
        return "This voice note was already processed. Its task has since been removed."
    if status == "failed":
        return "⚠️ This voice note previously failed. Please record and send a new voice message."
    return "This voice note was already received. Check my original reply for its result."


def welcome(dashboard_url: str) -> str:
    text = "👋 Hi! I turn your messages into tasks.\n\n" + HELP
    if dashboard_url:
        text += f"\n\nDashboard: {dashboard_url}"
    return text


def voice_too_long(max_seconds: int) -> str:
    return f"⏱ That voice message is too long. Please keep it under {max_seconds} seconds."


def voice_too_big(max_mb: int) -> str:
    return f"📦 That voice message is too large (limit {max_mb} MB)."


def task_created(task_id: int, text: str) -> str:
    return f"✅ Task #{task_id} added:\n“{text}”"


def voice_task_created(task_id: int, text: str) -> str:
    return f"✅ Task #{task_id} added from your voice note:\n“{text}”"


def status_changed(task_id: int, title: str, status: str) -> str:
    return f"📌 Task #{task_id} “{title}” is now {STATUS_LABELS.get(status, status)}"


# Failure messages for the voice path, keyed by failure kind (see worker.jobs).
VOICE_FAILURES = {
    "invalid_audio": (
        "🤔 I couldn't make out any speech in that voice message. "
        "Try again a bit closer to the mic, or just type the task."
    ),
    "unavailable": (
        "⚠️ Transcription is temporarily unavailable, so I couldn't add that task. "
        "Please try again in a minute, or type it instead."
    ),
    "misconfigured": ("⚠️ Voice transcription isn't available right now. Please type the task instead."),
    "unknown": "⚠️ I couldn't process that voice message. Please try again, or type the task.",
}
