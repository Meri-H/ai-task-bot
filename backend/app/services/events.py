"""Real-time task events: published to Redis pub/sub, fanned out to dashboards by the API."""

import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.models import Task
from app.schemas import TaskRead

logger = logging.getLogger(__name__)

EVENTS_CHANNEL = "taskbot:events"

TASK_CREATED = "task.created"
TASK_UPDATED = "task.updated"
TASK_DELETED = "task.deleted"


def build_task_event(event_type: str, task: Task) -> str:
    payload = (
        {"type": event_type, "task_id": task.id}
        if event_type == TASK_DELETED
        else {"type": event_type, "task": TaskRead.model_validate(task).model_dump(mode="json")}
    )
    return json.dumps(payload)


async def publish_task_event(redis: Redis, event_type: str, task: Task) -> None:
    """Best effort: the task is already committed, so a Redis hiccup must not fail the request or
    job. Dashboards re-sync from the REST API whenever their socket reconnects."""
    try:
        await redis.publish(EVENTS_CHANNEL, build_task_event(event_type, task))
    except RedisError:
        logger.warning("could not publish %s for task %s", event_type, task.id, exc_info=True)
