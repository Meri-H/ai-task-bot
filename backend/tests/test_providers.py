import httpx
import pytest

from app.core.config import Settings
from app.transcription import (
    FallbackTranscriber,
    InvalidAudioError,
    PermanentTranscriptionError,
    ProviderConfigError,
    TranscriptionResult,
    TransientTranscriptionError,
    build_transcriber,
)
from app.transcription.local_provider import LocalWhisperTranscriber
from app.transcription.openai_provider import OpenAITranscriber
from tests.fakes import ScriptedTranscriber


def _openai(handler, **kwargs) -> OpenAITranscriber:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAITranscriber("sk-test", client=client, **kwargs)


def _json(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


# --- OpenAI provider -------------------------------------------------------------------------


async def test_openai_success_sends_expected_request():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return _json(200, {"text": " buy milk ", "language": "english", "duration": 2.5})

    result = await _openai(handler).transcribe(b"audio-bytes")

    assert result == TranscriptionResult(text="buy milk", language="english", duration_seconds=2.5)
    assert seen["auth"] == "Bearer sk-test"
    assert seen["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert b'name="model"' in seen["body"] and b"whisper-1" in seen["body"]
    assert b"verbose_json" in seen["body"]
    assert b"audio-bytes" in seen["body"]


async def test_openai_non_whisper_model_uses_plain_json_and_language_hint():
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read())
        return _json(200, {"text": "hola"})

    t = _openai(handler, model="gpt-4o-mini-transcribe", language="es")
    result = await t.transcribe(b"x")
    assert result.text == "hola" and result.language is None
    assert b"verbose_json" not in bodies[0]
    assert b'name="language"' in bodies[0]


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (
            429,
            {"error": {"message": "slow down", "code": "rate_limit_exceeded"}},
            TransientTranscriptionError,
        ),
        (500, {"error": {"message": "oops"}}, TransientTranscriptionError),
        (503, {}, TransientTranscriptionError),
        (408, {}, TransientTranscriptionError),
        (429, {"error": {"message": "billing", "code": "insufficient_quota"}}, ProviderConfigError),
        (429, {"error": {"message": "no credits", "code": "credit_balance_exhausted"}}, ProviderConfigError),
        (429, {"error": {"code": "project_spend_limit_exceeded"}}, ProviderConfigError),
        (429, {"error": {"code": "organization_spend_limit_exceeded"}}, ProviderConfigError),
        (429, {"error": {"code": "organization_usage_limit_exceeded"}}, ProviderConfigError),
        (401, {"error": {"message": "bad key"}}, ProviderConfigError),
        (403, {}, ProviderConfigError),
        (400, {"error": {"message": "Invalid file format"}}, InvalidAudioError),
        (413, {}, InvalidAudioError),
        (404, {}, PermanentTranscriptionError),
    ],
)
async def test_openai_error_classification(status, body, expected):
    t = _openai(lambda request: _json(status, body))
    with pytest.raises(expected) as info:
        await t.transcribe(b"x")
    # Retryable-ness is decided purely by the class hierarchy.
    assert isinstance(info.value, TransientTranscriptionError) == (expected is TransientTranscriptionError)


async def test_openai_timeout_and_connection_errors_are_transient():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    for handler in (timeout, refused):
        with pytest.raises(TransientTranscriptionError):
            await _openai(handler).transcribe(b"x")


def test_openai_requires_api_key():
    with pytest.raises(ProviderConfigError):
        OpenAITranscriber("")


# --- Local provider (model injected, no download) ------------------------------------------


class _Seg:
    def __init__(self, text):
        self.text = text


class _FakeModel:
    def __init__(self, segments=None, error=None):
        self._segments, self._error = segments or [], error

    def transcribe(self, audio, **kwargs):
        if self._error:
            raise self._error
        info = type("Info", (), {"language": "en", "duration": 3.0})()
        return iter(self._segments), info


async def test_local_joins_segments():
    t = LocalWhisperTranscriber()
    t._model = _FakeModel([_Seg(" call "), _Seg("mom ")])
    result = await t.transcribe(b"x")
    assert (result.text, result.language, result.duration_seconds) == ("call mom", "en", 3.0)


async def test_local_decode_errors_are_invalid_audio_others_transient():
    class DecodeError(Exception):
        pass

    DecodeError.__module__ = "av.error"

    t = LocalWhisperTranscriber()
    t._model = _FakeModel(error=DecodeError("invalid data"))
    with pytest.raises(InvalidAudioError):
        await t.transcribe(b"x")

    t._model = _FakeModel(error=RuntimeError("out of memory"))
    with pytest.raises(TransientTranscriptionError):
        await t.transcribe(b"x")


# --- Fallback + factory ---------------------------------------------------------------------


async def test_fallback_uses_secondary_when_primary_is_down_or_misconfigured():
    for error in (TransientTranscriptionError("down"), ProviderConfigError("bad key")):
        primary, secondary = ScriptedTranscriber(error), ScriptedTranscriber("from local")
        result = await FallbackTranscriber(primary, secondary).transcribe(b"x")
        assert result.text == "from local"
        assert (primary.calls, secondary.calls) == (1, 1)


async def test_fallback_does_not_retry_bad_audio_elsewhere():
    primary, secondary = ScriptedTranscriber(InvalidAudioError("garbage")), ScriptedTranscriber("never")
    with pytest.raises(InvalidAudioError):
        await FallbackTranscriber(primary, secondary).transcribe(b"x")
    assert secondary.calls == 0


async def test_fallback_skips_secondary_when_primary_succeeds():
    primary, secondary = ScriptedTranscriber("ok"), ScriptedTranscriber("never")
    assert (await FallbackTranscriber(primary, secondary).transcribe(b"x")).text == "ok"
    assert secondary.calls == 0


def test_factory_selection():
    base = {"database_url": "postgresql+asyncpg://x/y"}
    assert isinstance(build_transcriber(Settings(**base)), LocalWhisperTranscriber)
    assert isinstance(build_transcriber(Settings(**base, openai_api_key="k")), OpenAITranscriber)
    both = build_transcriber(Settings(**base, openai_api_key="k", transcription_fallback_to_local=True))
    assert isinstance(both, FallbackTranscriber) and both.name == "openai+local"
    assert isinstance(
        build_transcriber(Settings(**base, openai_api_key="k", transcription_provider="local")),
        LocalWhisperTranscriber,
    )
    with pytest.raises(ProviderConfigError):
        build_transcriber(Settings(**base, transcription_provider="openai"))


def test_local_decoder_accepts_real_audio_without_metadata_errors():
    """Exercise the actual faster-whisper/PyAV boundary without downloading model weights."""
    import io
    import wave

    pytest.importorskip("av")
    audio_module = pytest.importorskip("faster_whisper.audio")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)
    audio.seek(0)
    decoded = audio_module.decode_audio(audio)
    assert len(decoded) == 1600
