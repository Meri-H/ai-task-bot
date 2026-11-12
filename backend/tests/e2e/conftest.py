"""Boots the real stack as separate OS processes: API, Telegram bot, arq worker.

Only the outside world is faked (Telegram + OpenAI, see fake_services.py). Postgres and Redis are
the real thing, and the three app processes are started exactly like the Docker containers do.
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import asyncpg
import httpx
import pytest
from arq.constants import health_check_key_suffix
from redis import Redis as SyncRedis
from redis.asyncio import Redis

from app.core.queue import NOTIFICATION_QUEUE_NAME, QUEUE_NAME
from tests.conftest import TEST_DATABASE_URL, TEST_REDIS_URL

BACKEND_DIR = Path(__file__).resolve().parents[2]
STARTUP_TIMEOUT = float(os.environ.get("E2E_STARTUP_TIMEOUT_SECONDS", "90"))
WORKER_QUEUES = {"worker": QUEUE_NAME, "notifications": NOTIFICATION_QUEUE_NAME}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stack:
    def __init__(self, log_dir: Path) -> None:
        self.fake_port, self.api_port = _free_port(), _free_port()
        self.fake = f"http://127.0.0.1:{self.fake_port}"
        self.api = f"http://127.0.0.1:{self.api_port}"
        self.log_dir = log_dir
        self.procs: dict[str, subprocess.Popen] = {}
        self.commands: dict[str, tuple[str, ...]] = {}
        self.env = {
            **os.environ,
            "DATABASE_URL": TEST_DATABASE_URL,
            "REDIS_URL": TEST_REDIS_URL,
            "TELEGRAM_BOT_TOKEN": "123456:E2E-TOKEN",
            "TELEGRAM_API_BASE": self.fake,
            "TRANSCRIPTION_PROVIDER": "openai",
            "OPENAI_API_KEY": "sk-e2e",
            "OPENAI_BASE_URL": f"{self.fake}/v1",
            "RETRY_BACKOFF_BASE_SECONDS": "0.2",
            "JOB_MAX_TRIES": "3",
            "DASHBOARD_TOKEN": "",
            "MAX_VOICE_SECONDS": "60",
            "LOG_LEVEL": "INFO",
            "PYTHONUNBUFFERED": "1",
        }
        self.http = httpx.Client(timeout=5)
        self.redis = SyncRedis.from_url(TEST_REDIS_URL, socket_timeout=1, socket_connect_timeout=1)

    def _spawn(self, name: str, *cmd: str) -> None:
        self.commands[name] = cmd
        if name in WORKER_QUEUES:
            # A previous worker's expiring heartbeat must not make a replacement look ready.
            self.redis.delete(WORKER_QUEUES[name] + health_check_key_suffix)
        with open(self.log_dir / f"{name}.log", "w") as log:
            self.procs[name] = subprocess.Popen(
                cmd, cwd=BACKEND_DIR, env=self.env, stdout=log, stderr=subprocess.STDOUT
            )

    def _worker_ready(self, name: str) -> bool:
        return bool(self.redis.exists(WORKER_QUEUES[name] + health_check_key_suffix))

    def _bot_ready(self) -> bool:
        try:
            response = self.http.get(f"{self.fake}/_control/health")
            return response.status_code == 200 and response.json().get("bot_polling", False)
        except (httpx.HTTPError, ValueError):
            return False

    def start(self) -> None:
        # One bounded startup budget covers all services on slower Docker Desktop hosts.
        deadline = time.monotonic() + STARTUP_TIMEOUT

        def ready(predicate, description):
            return self.wait_for(
                predicate,
                description,
                timeout=max(0, deadline - time.monotonic()),
                processes=tuple(self.procs),
            )

        py = sys.executable
        bin_dir = os.path.dirname(py)
        self._spawn("fake", py, "-m", "tests.e2e.fake_services", "--port", str(self.fake_port))
        ready(lambda: self._ok(f"{self.fake}/_control/health"), "fake services up")
        self._spawn(
            "api",
            py,
            "-m",
            "uvicorn",
            "app.api.main:app",
            "--port",
            str(self.api_port),
            "--log-level",
            "warning",
        )
        self._spawn("worker", os.path.join(bin_dir, "arq"), "app.worker.settings.WorkerSettings")
        self._spawn(
            "notifications", os.path.join(bin_dir, "arq"), "app.worker.settings.NotificationWorkerSettings"
        )
        self._spawn("bot", py, "-m", "app.bot.main")
        ready(lambda: self._ok(f"{self.api}/api/health"), "API healthy")
        ready(lambda: self._worker_ready("worker"), "voice worker heartbeat after startup")
        ready(lambda: self._worker_ready("notifications"), "notification worker heartbeat after startup")
        ready(self._bot_ready, "bot polling the fake Telegram server")

    def stop_service(self, name: str) -> None:
        proc = self.procs[name]
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)

    def restart_service(self, name: str) -> None:
        self.stop_service(name)
        self._spawn(name, *self.commands[name])
        if name not in {"api", "bot", *WORKER_QUEUES}:
            return

        def ready():
            if name == "api":
                return self._ok(f"{self.api}/api/health")
            if name in WORKER_QUEUES:
                return self._worker_ready(name)
            return self._bot_ready()

        self.wait_for(ready, f"restarted {name} ready", timeout=STARTUP_TIMEOUT, processes=(name,))

    def stop(self) -> None:
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs.values():
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        self.http.close()
        self.redis.close()

    # -- helpers -------------------------------------------------------------------------------

    def _ok(self, url: str) -> bool:
        try:
            return self.http.get(url).status_code == 200
        except httpx.HTTPError:
            return False

    def logs(self, name: str | None = None, tail: int = 40) -> str:
        names = [name] if name else list(self.procs)
        out = []
        for n in names:
            path = self.log_dir / f"{n}.log"
            lines = path.read_text(errors="replace").splitlines() if path.exists() else []
            proc = self.procs.get(n)
            state = "not started" if proc is None else f"pid={proc.pid}, exit={proc.poll()}"
            out.append(f"--- {n} log ({state}; last {tail} lines) ---\n" + "\n".join(lines[-tail:]))
        return "\n".join(out) if not name else out[0]

    def wait_for(
        self,
        predicate: Callable[[], object],
        desc: str,
        timeout: float = 15.0,
        *,
        processes: tuple[str, ...] = (),
    ):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for name in processes:
                proc = self.procs[name]
                if (code := proc.poll()) is not None:
                    raise AssertionError(
                        f"{name} exited with code {code} while waiting for {desc}\n{self.logs()}"
                    )
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for: {desc} (budget {timeout:.1f}s)\n{self.logs()}")

    # -- control of the fakes ------------------------------------------------------------------

    async def _queues_empty(self) -> bool:
        async with Redis.from_url(TEST_REDIS_URL) as redis:
            return await redis.zcard(QUEUE_NAME) == 0 and await redis.zcard(NOTIFICATION_QUEUE_NAME) == 0

    def reset(self) -> None:
        self.wait_for(lambda: asyncio.run(self._queues_empty()), "previous jobs completed", timeout=20)
        self.http.post(f"{self.fake}/_control/reset")
        asyncio.run(self._sql("TRUNCATE users, tasks, transcription_jobs RESTART IDENTITY CASCADE"))

    def send_text(self, text: str, *, message_id: int, user_id: int = 1, chat_id: int | None = None) -> dict:
        return self.http.post(
            f"{self.fake}/_control/user_update",
            json={
                "kind": "text",
                "text": text,
                "message_id": message_id,
                "user_id": user_id,
                "chat_id": chat_id or user_id,
            },
        ).json()

    def send_voice(
        self,
        content: str,
        *,
        message_id: int,
        user_id: int = 1,
        chat_id: int | None = None,
        duration: int = 3,
    ) -> dict:
        return self.http.post(
            f"{self.fake}/_control/user_update",
            json={
                "kind": "voice",
                "content": content,
                "file_id": f"voice-{message_id}",
                "message_id": message_id,
                "user_id": user_id,
                "chat_id": chat_id or user_id,
                "duration": duration,
            },
        ).json()

    def fail_openai(self, *statuses: int, always: int | None = None) -> None:
        self.http.post(
            f"{self.fake}/_control/openai_failures", json={"statuses": list(statuses), "always": always}
        )

    def fake_state(self) -> dict:
        return self.http.get(f"{self.fake}/_control/state").json()

    def bot_messages(self) -> list[dict]:
        return self.fake_state()["sent"]

    def bot_edits(self) -> list[dict]:
        return self.fake_state()["edits"]

    def tasks(self) -> list[dict]:
        return self.http.get(f"{self.api}/api/tasks").json()["items"]

    async def _sql(self, query: str):
        conn = await asyncpg.connect(TEST_DATABASE_URL.replace("+asyncpg", ""))
        try:
            return await conn.fetch(query)
        finally:
            await conn.close()

    def sql(self, query: str):
        return asyncio.run(self._sql(query))


@pytest.fixture(scope="module")
def stack(tmp_path_factory, migrated_db) -> Iterator[Stack]:
    stack = Stack(tmp_path_factory.mktemp("e2e-logs"))
    try:
        stack.start()
        yield stack
    finally:
        stack.stop()


@pytest.fixture(autouse=True)
def _fresh_state(stack: Stack) -> None:
    stack.reset()
