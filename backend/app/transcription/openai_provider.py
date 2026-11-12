import logging

import httpx

from app.transcription.base import (
    InvalidAudioError,
    PermanentTranscriptionError,
    ProviderConfigError,
    TranscriptionResult,
    TransientTranscriptionError,
)

logger = logging.getLogger(__name__)


class OpenAITranscriber:
    """OpenAI `/audio/transcriptions` (Whisper) over plain HTTPS.

    We use httpx directly instead of the official SDK: one endpoint, no extra dependency, and
    full control over timeouts and how each failure maps to retry/no-retry.
    """

    name = "openai"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "whisper-1",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        language: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ProviderConfigError("OPENAI_API_KEY is not set")
        self._api_key = api_key
        self._model = model
        self._url = base_url.rstrip("/") + "/audio/transcriptions"
        self._language = language
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))

    async def warmup(self) -> None:
        return None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> TranscriptionResult:
        data = {
            "model": self._model,
            # Only whisper-1 supports verbose_json (which includes the detected language).
            "response_format": "verbose_json" if self._model == "whisper-1" else "json",
        }
        if self._language:
            data["language"] = self._language

        try:
            response = await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": (filename, audio, "audio/ogg")},
                data=data,
            )
        except httpx.TimeoutException as exc:
            raise TransientTranscriptionError("OpenAI request timed out") from exc
        except httpx.TransportError as exc:
            raise TransientTranscriptionError(f"OpenAI connection error: {exc}") from exc

        if response.status_code == 200:
            payload = response.json()
            return TranscriptionResult(
                text=(payload.get("text") or "").strip(),
                language=payload.get("language"),
                duration_seconds=payload.get("duration"),
            )
        raise self._error_for(response)

    @staticmethod
    def _error_for(response: httpx.Response) -> Exception:
        status = response.status_code
        try:
            detail = response.json().get("error", {})
            message = detail.get("message") or response.text[:200]
            code = detail.get("code") or ""
        except ValueError:
            message, code = response.text[:200], ""
        summary = f"OpenAI HTTP {status}: {message}"

        if status in (401, 403):
            return ProviderConfigError(summary)
        if status == 429:
            # "insufficient_quota" is a billing problem, not a rate limit: retrying is pointless.
            if code in {
                "insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
                "project_spend_limit_exceeded", "organization_usage_limit_exceeded",
            }:
                return ProviderConfigError(summary)
            return TransientTranscriptionError(summary)
        if status in (408, 409) or status >= 500:
            return TransientTranscriptionError(summary)
        if status in (400, 413, 415, 422):
            return InvalidAudioError(summary)
        return PermanentTranscriptionError(summary)
