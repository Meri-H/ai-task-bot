import re
from datetime import datetime

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, TaskSource, TaskStatus
from app.models.base import utcnow

TITLE_MAX = 200


def split_title_description(text: str) -> tuple[str, str | None]:
    """Turn free text into (title, description).

    Short single-line text is just the title. Longer text keeps a trimmed first line/sentence as
    the title and the full original text as the description, so nothing is ever lost.
    """
    text = text.strip()
    first_line = text.splitlines()[0].strip() if text else ""
    if len(first_line) <= TITLE_MAX and first_line == text:
        return text, None
    title = first_line
    if len(title) > TITLE_MAX:
        cut = title[: TITLE_MAX - 1]
        # Prefer cutting at a word boundary.
        title = (re.sub(r"\s+\S*$", "", cut) or cut).rstrip() + "…"
    return title, text


async def create_task(
    session: AsyncSession,
    *,
    title: str,
    source: TaskSource,
    description: str | None = None,
    user_id: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[Task, bool]:
    """Insert a task. Returns (task, created).

    With an idempotency key the insert is `ON CONFLICT DO NOTHING`, so concurrent or repeated
    calls (queue retries, Telegram re-delivering an update) resolve to the same row.
    """
    stmt = (
        pg_insert(Task)
        .values(
            title=title,
            description=description,
            status=TaskStatus.PENDING.value,
            source=source.value,
            user_id=user_id,
            idempotency_key=idempotency_key,
        )
        .on_conflict_do_nothing(index_elements=[Task.idempotency_key])
        .returning(Task.id)
    )
    new_id = (await session.execute(stmt)).scalar_one_or_none()
    if new_id is not None:
        task = await session.get(Task, new_id, populate_existing=True)
        assert task is not None
        return task, True

    existing = (
        await session.execute(
            select(Task).where(Task.idempotency_key == idempotency_key),
            execution_options={"populate_existing": True},
        )
    ).scalar_one()
    return existing, False


async def get_task(session: AsyncSession, task_id: int) -> Task | None:
    return await session.get(Task, task_id, populate_existing=True)


async def list_tasks(
    session: AsyncSession,
    *,
    status: TaskStatus | None = None,
    user_id: int | None = None,
    limit: int = 200,
    offset: int = 0,
    before_created_at: datetime | None = None,
    before_id: int | None = None,
) -> tuple[list[Task], int]:
    filters = []
    if status is not None:
        filters.append(Task.status == status.value)
    if user_id is not None:
        filters.append(Task.user_id == user_id)

    total = (await session.execute(select(func.count(Task.id)).where(*filters))).scalar_one()
    page_filters = list(filters)
    if before_created_at is not None and before_id is not None:
        page_filters.append(tuple_(Task.created_at, Task.id) < (before_created_at, before_id))
    rows = (
        (
            await session.execute(
                select(Task)
                .where(*page_filters)
                .order_by(Task.created_at.desc(), Task.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .unique()
    )
    return list(rows), total


async def update_status(session: AsyncSession, task: Task, new_status: TaskStatus) -> bool:
    """Set the status. Returns True if it actually changed."""
    if task.status == new_status.value:
        return False
    now = utcnow()
    task.status = new_status.value
    task.updated_at = now
    task.completed_at = now if new_status is TaskStatus.COMPLETED else None
    await session.flush()
    return True
