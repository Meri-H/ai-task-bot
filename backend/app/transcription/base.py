"""Transcription contract and error taxonomy.

The worker only cares about *whether an error is worth retrying*, so providers translate their
own failures (HTTP codes, decode errors, timeouts) into this small hierarchy:

    TranscriptionError
    ├── TransientTranscriptionError      retry with backoff (network, 429, 5xx, timeouts)
    └── PermanentTranscriptionError      never retry
        ├── InvalidAudioError            the audio itself is the problem (corrupt, silent, empty)
        └── ProviderConfigError          bad API key / missing model / quota: retrying won't help,
                                         but a *different* provider might
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class TranscriptionError(Exception):
    """Base class for every transcription failure."""


class TransientTranscriptionError(TranscriptionError):
    """Temporary failure; the job should be retried."""


class PermanentTranscriptionError(TranscriptionError):
    """Failure that retrying the same request will not fix."""


class InvalidAudioError(PermanentTranscriptionError):
    """The audio could not be decoded or contained no usable speech."""


class EmptyTranscriptError(InvalidAudioError):
    """Transcription succeeded technically but produced no text."""


class ProviderConfigError(PermanentTranscriptionError):
    """The provider is unusable as configured (auth, quota, missing dependency)."""


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    language: str | None = None
    duration_seconds: float | None = None


@runtime_checkable
class Transcriber(Protocol):
    name: str

    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> TranscriptionResult: ...

    async def warmup(self) -> None:
        """Optional: load models / open connections ahead of the first job."""
        ...
