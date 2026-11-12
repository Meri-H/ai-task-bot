"""Test doubles for Telegram and the transcription provider."""

from types import SimpleNamespace

from app.transcription import TranscriptionResult


class FakeBot:
    """Just enough of aiogram.Bot for the worker: get_file/download_file/edit/send."""

    def __init__(
        self, audio: bytes = b"OggS-fake-audio", *, get_file_error=None, edit_error=None, send_error=None
    ):
        self.audio = audio
        self.get_file_error = get_file_error
        self.edit_error = edit_error
        self.send_error = send_error
        self.edits: list[tuple[int, int | None, str]] = []
        self.sent: list[tuple[int, str]] = []

    async def get_file(self, file_id: str):
        if self.get_file_error:
            raise self.get_file_error
        return SimpleNamespace(file_path=f"voice/{file_id}.oga")

    async def download_file(self, file_path: str, destination):
        destination.write(self.audio)

    async def edit_message_text(self, text: str, chat_id: int, message_id: int):
        if self.edit_error:
            raise self.edit_error
        self.edits.append((chat_id, message_id, text))

    async def send_message(self, chat_id: int, text: str):
        if self.send_error:
            raise self.send_error
        self.sent.append((chat_id, text))


class ScriptedTranscriber:
    """Returns/raises the next scripted outcome on every call (the last one repeats)."""

    name = "scripted"

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def warmup(self) -> None:
        return None

    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> TranscriptionResult:
        outcome = self._outcomes[min(self.calls, len(self._outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, str):
            return TranscriptionResult(text=outcome, language="en", duration_seconds=1.0)
        return outcome
