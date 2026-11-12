import asyncio
import io
import logging
import threading
from typing import Any

from app.transcription.base import (
    InvalidAudioError,
    ProviderConfigError,
    TranscriptionResult,
    TransientTranscriptionError,
)

logger = logging.getLogger(__name__)


class LocalWhisperTranscriber:
    """Offline transcription with faster-whisper (CTranslate2).

    Needs no API key. The model is loaded once (see `warmup`) and inference runs in a worker
    thread so the event loop stays responsive. A semaphore serialises inference: one Whisper
    decode already uses all CPU cores, so running several at once only slows each down.
    """

    name = "local"

    def __init__(
        self,
        model_size: str = "base",
        *,
        device: str = "cpu",
        compute_type: str = "int8",
        download_root: str | None = None,
        language: str = "",
        concurrency: int = 1,
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._download_root = download_root
        self._language = language or None
        self._model: Any = None
        self._load_lock = threading.Lock()
        self._semaphore = asyncio.Semaphore(concurrency)

    def _load(self) -> Any:
        with self._load_lock:
            if self._model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise ProviderConfigError(
                        "faster-whisper is not installed (pip install '.[local]')"
                    ) from exc
                logger.info("loading faster-whisper model %r on %s", self._model_size, self._device)
                self._model = WhisperModel(
                    self._model_size,
                    device=self._device,
                    compute_type=self._compute_type,
                    download_root=self._download_root,
                )
                logger.info("faster-whisper model ready")
            return self._model

    async def warmup(self) -> None:
        await asyncio.to_thread(self._load)

    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> TranscriptionResult:
        async with self._semaphore:
            return await asyncio.to_thread(self._run, audio)

    def _run(self, audio: bytes) -> TranscriptionResult:
        try:
            model = self._load()
        except ProviderConfigError:
            raise
        except Exception as exc:  # e.g. model download failed: usually a network problem
            raise TransientTranscriptionError(f"could not load whisper model: {exc}") from exc

        try:
            segments, info = model.transcribe(
                io.BytesIO(audio),
                language=self._language,
                beam_size=1,
                vad_filter=True,  # skip silence: faster, and avoids hallucinated text on silence
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            # PyAV raises its own error types when the container/codec cannot be decoded.
            if type(exc).__module__.split(".")[0] == "av":
                raise InvalidAudioError(f"could not decode audio: {exc}") from exc
            raise TransientTranscriptionError(f"local transcription failed: {exc}") from exc

        return TranscriptionResult(text=text, language=info.language, duration_seconds=float(info.duration))
