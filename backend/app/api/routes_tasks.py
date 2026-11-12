import logging
from datetime import datetime

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_redis, get_session, require_auth
from app.core.queue import enqueue_status_notification
from app.models import TaskSource, TaskStatus
from app.schemas import TaskCreate, TaskList, TaskRead, TaskUpdate
from app.services import tasks as task_service
from app.services.events import TASK_CREATED, TASK_DELETED, TASK_UPDATED, publish_task_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(require_auth)])


@router.get("", response_model=TaskList)
async def list_tasks(
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    before_created_at: datetime | None = Query(default=None),
    before_id: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_session),
) -> TaskList:
    if (before_created_at is None) != (before_id is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Provide both cursor fields")
    if before_created_at is not None and (before_created_at.utcoffset() is None or offset):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Cursor requires a timezone and cannot be combined with offset",
        )
    items, total = await task_service.list_tasks(
        session, status=status_filter, limit=limit, offset=offset,
        before_created_at=before_created_at, before_id=before_id,
    )
    return TaskList(items=[TaskRead.model_validate(t) for t in items], total=total)


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(task_id: int, session: AsyncSession = Depends(get_session)):
    task = await task_service.get_task(session, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return task


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    session: AsyncSession = Depends(get_session),
    redis: ArqRedis = Depends(get_redis),
):
    task, _ = await task_service.create_task(
        session, title=payload.title, description=payload.description, source=TaskSource.WEB
    )
    await session.commit()
    await publish_task_event(redis, TASK_CREATED, task)
    return task


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: int,
    payload: TaskUpdate,
    session: AsyncSession = Depends(get_session),
    redis: ArqRedis = Depends(get_redis),
):
    task = await task_service.get_task(session, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")

    changed = await task_service.update_status(session, task, payload.status)
    await session.commit()

    if changed:
        await publish_task_event(redis, TASK_UPDATED, task)
        # Let the Telegram author know, asynchronously (the dashboard click must not wait on
        # Telegram). Tasks created on the web have no Telegram user, so nothing to notify.
        if task.user is not None:
            try:
                await enqueue_status_notification(redis, task.id, task.status)
            except RedisError:
                logger.warning("could not queue notification for task %s", task.id, exc_info=True)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    redis: ArqRedis = Depends(get_redis),
) -> Response:
    task = await task_service.get_task(session, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    # Voice-job history is retained; its task_id is cleared by ON DELETE SET NULL.
    await session.delete(task)
    await session.commit()
    await publish_task_event(redis, TASK_DELETED, task)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
