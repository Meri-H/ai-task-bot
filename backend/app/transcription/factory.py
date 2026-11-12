import logging

from app.core.config import Settings
from app.transcription.base import (
    ProviderConfigError,
    Transcriber,
    TranscriptionResult,
    TransientTranscriptionError,
)
from app.transcription.local_provider import LocalWhisperTranscriber
from app.transcription.openai_provider import OpenAITranscriber

logger = logging.getLogger(__name__)


class FallbackTranscriber:
    """Try the primary provider; if it is down or misconfigured, use the secondary.

    Bad audio is *not* retried on the secondary: it would fail there too.
    """

    def __init__(self, primary: Transcriber, secondary: Transcriber) -> None:
        self._primary = primary
        self._secondary = secondary
        self.name = f"{primary.name}+{secondary.name}"

    async def warmup(self) -> None:
        await self._primary.warmup()
        try:
            await self._secondary.warmup()
        except Exception:
            logger.warning("fallback transcriber warmup failed", exc_info=True)

    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> TranscriptionResult:
        try:
            return await self._primary.transcribe(audio, filename)
        except (TransientTranscriptionError, ProviderConfigError) as exc:
            logger.warning(
                "%s transcription failed (%s); falling back to %s",
                self._primary.name,
                exc,
                self._secondary.name,
            )
            return await self._secondary.transcribe(audio, filename)


def _local(settings: Settings) -> LocalWhisperTranscriber:
    return LocalWhisperTranscriber(
        settings.whisper_local_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        download_root=settings.whisper_model_dir,
        language=settings.transcription_language,
    )


def _openai(settings: Settings) -> OpenAITranscriber:
    return OpenAITranscriber(
        settings.openai_api_key,
        model=settings.openai_transcribe_model,
        base_url=settings.openai_base_url,
        timeout=settings.transcription_timeout_seconds,
        language=settings.transcription_language,
    )


def build_transcriber(settings: Settings) -> Transcriber:
    provider = settings.transcription_provider
    if provider == "openai":
        return _openai(settings)  # raises ProviderConfigError if no key
    if provider == "local":
        return _local(settings)

    # auto
    if settings.openai_api_key:
        primary = _openai(settings)
        if settings.transcription_fallback_to_local:
            return FallbackTranscriber(primary, _local(settings))
        return primary
    return _local(settings)
