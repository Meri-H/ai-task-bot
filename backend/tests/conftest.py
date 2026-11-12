"""Shared fixtures.

Integration tests run against a real PostgreSQL and Redis (no SQLite stand-in: the code uses
Postgres-specific `INSERT .. ON CONFLICT`). Point them elsewhere with TEST_DATABASE_URL /
TEST_REDIS_URL. If the services are not reachable the tests are skipped with an explicit reason.
"""

import asyncio
import os
import socket
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://taskbot:taskbot@localhost:5432/taskbot_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")

# Must be set before app.core.config.get_settings() is first called (it is cached).
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["REDIS_URL"] = TEST_REDIS_URL
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:TEST"
os.environ["TRANSCRIPTION_PROVIDER"] = "auto"
os.environ["OPENAI_API_KEY"] = ""
os.environ["TRANSCRIPTION_FALLBACK_TO_LOCAL"] = "false"

from arq.connections import ArqRedis  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.db import create_engine, create_session_factory  # noqa: E402
from app.core.queue import create_queue_pool  # noqa: E402


def _reachable(url: str, default_port: int) -> bool:
    parsed = urlparse(url.replace("+asyncpg", ""))
    try:
        with socket.create_connection((parsed.hostname or "localhost", parsed.port or default_port), 1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
        telegram_bot_token="123456:TEST",
        job_max_tries=3,
        retry_backoff_base_seconds=0.02,
        dashboard_token="",
    )


@pytest.fixture(scope="session")
def migrated_db(settings: Settings) -> None:
    """Build the schema with the real Alembic migrations (so they are exercised by every run)."""
    def unavailable(reason: str) -> None:
        if os.environ.get("REQUIRE_INTEGRATION_TESTS") == "true":
            pytest.fail(reason)
        pytest.skip(reason, allow_module_level=True)

    if not _reachable(settings.database_url, 5432):
        unavailable("PostgreSQL not reachable (set TEST_DATABASE_URL)")
    if not _reachable(settings.redis_url, 6379):
        unavailable("Redis not reachable (set TEST_REDIS_URL)")

    from alembic.config import Config

    from alembic import command

    async def _reset() -> None:
        engine = create_engine(settings.database_url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(_reset())
    command.upgrade(Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini")), "head")


async def _truncate(settings: Settings) -> None:
    engine = create_engine(settings.database_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE users, tasks, transcription_jobs RESTART IDENTITY CASCADE"))
    await engine.dispose()
    redis = await create_queue_pool(settings.redis_url)
    await redis.flushdb()
    await redis.aclose()


@pytest.fixture
async def clean_db(settings: Settings, migrated_db: None) -> None:
    await _truncate(settings)


@pytest.fixture
def clean_db_sync(settings: Settings, migrated_db: None) -> None:
    asyncio.run(_truncate(settings))


@pytest.fixture
async def engine(settings: Settings, clean_db: None) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings.database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
async def redis(settings: Settings, clean_db: None) -> AsyncIterator[ArqRedis]:
    client = await create_queue_pool(settings.redis_url)
    yield client
    await client.aclose()
