"""Fan-out of Redis pub/sub task events to connected WebSocket clients.

One Redis subscription per API process (not per browser tab), then an in-process queue per
client. A slow client that fills its queue is told to re-sync instead of blocking everyone else.
"""

import asyncio
import contextlib
import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.services.events import EVENTS_CHANNEL

logger = logging.getLogger(__name__)

CLIENT_QUEUE_SIZE = 100
RESYNC_MESSAGE = json.dumps({"type": "resync"})


class EventHub:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._clients: set[asyncio.Queue[str]] = set()
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="event-hub")
        # Don't accept traffic until we are subscribed, or early events would be missed.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout=5)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=CLIENT_QUEUE_SIZE)
        self._clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._clients.discard(queue)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def _broadcast(self, payload: str) -> None:
        for queue in list(self._clients):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop what the client missed and tell it to reload from the REST API.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(RESYNC_MESSAGE)

    async def _run(self) -> None:
        delay = 1.0
        while True:
            try:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(EVENTS_CHANNEL)
                self._ready.set()
                delay = 1.0
                logger.info("subscribed to %s", EVENTS_CHANNEL)
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    data = message["data"]
                    self._broadcast(data.decode() if isinstance(data, bytes) else data)
            except asyncio.CancelledError:
                raise
            except (RedisError, OSError):
                logger.warning("event subscription lost; retrying in %.0fs", delay, exc_info=True)
            # After an outage events may have been missed: make every client reload.
            self._broadcast(RESYNC_MESSAGE)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15.0)
