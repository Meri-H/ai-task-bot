"""Application settings, loaded from environment variables (and an optional .env file)."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _csv_ints(value: str) -> set[int]:
    return {int(part) for part in value.replace(" ", "").split(",") if part}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Infrastructure -------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://taskbot:taskbot@localhost:5432/taskbot"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"

    # --- Telegram -------------------------------------------------------------------------
    telegram_bot_token: str = ""
    # Override to talk to a self-hosted Bot API server (also used by the e2e fake server).
    telegram_api_base: str = "https://api.telegram.org"
    # Comma-separated Telegram user ids allowed to use the bot. Empty = everyone.
    telegram_allowed_user_ids: str = ""
    dashboard_url: str = ""  # shown in /start, e.g. http://localhost:3000

    # --- Voice limits ---------------------------------------------------------------------
    max_voice_seconds: int = 120
    max_voice_bytes: int = 20 * 1024 * 1024  # Telegram bots cannot download files above 20 MB

    # --- Transcription --------------------------------------------------------------------
    # auto: OpenAI when OPENAI_API_KEY is set, otherwise local faster-whisper.
    transcription_provider: Literal["auto", "openai", "local"] = "auto"
    # With `auto` + an OpenAI key: also fall back to the local model if OpenAI fails.
    transcription_fallback_to_local: bool = False
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_transcribe_model: str = "whisper-1"
    whisper_local_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_model_dir: str | None = None
    # Optional language hint (ISO-639-1, e.g. "en"). Empty = auto-detect.
    transcription_language: str = ""

    # --- Worker / resilience --------------------------------------------------------------
    worker_concurrency: int = 4
    notification_worker_concurrency: int = 4
    job_max_tries: int = 3
    retry_backoff_base_seconds: float = 2.0  # delay = base * 2 ** (attempt - 1)
    download_timeout_seconds: float = 30.0
    transcription_timeout_seconds: float = 90.0
    job_timeout_seconds: int = 180

    # --- API ------------------------------------------------------------------------------
    # If set, the REST API and WebSocket require `Authorization: Bearer <token>`.
    dashboard_token: str = ""
    cors_origins: str = ""  # comma-separated; empty = same-origin only

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg(cls, value: str) -> str:
        for prefix in ("postgresql://", "postgres://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix) :]
        return value

    @property
    def allowed_user_ids(self) -> set[int]:
        return _csv_ints(self.telegram_allowed_user_ids)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def backoff_seconds(self, attempt: int) -> float:
        return self.retry_backoff_base_seconds * (2 ** max(attempt - 1, 0))


@lru_cache
def get_settings() -> Settings:
    return Settings()
