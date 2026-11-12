import pytest

from app.models import TaskSource, TaskStatus
from app.services import tasks as svc
from app.services.users import TelegramSender, upsert_user


@pytest.mark.parametrize(
    ("text", "title", "has_description"),
    [
        ("Buy milk", "Buy milk", False),
        ("  padded  ", "padded", False),
        ("First line\nsecond line", "First line", True),
        ("x " * 150, None, True),  # long single line: truncated title, full text kept
    ],
)
def test_split_title_description(text, title, has_description):
    got_title, description = svc.split_title_description(text)
    assert len(got_title) <= svc.TITLE_MAX
    if title is not None:
        assert got_title == title
    else:
        assert got_title.endswith("…")
    assert (description is not None) == has_description
    if description is not None:
        assert description == text.strip()


async def test_create_task_defaults_to_pending(session_factory):
    async with session_factory() as session:
        task, created = await svc.create_task(session, title="Write report", source=TaskSource.WEB)
        await session.commit()
    assert created
    assert task.id == 1
    assert task.status == TaskStatus.PENDING
    assert task.user is None
    assert task.created_at is not None


async def test_create_task_is_idempotent_for_same_key(session_factory):
    async with session_factory() as session:
        first, created_first = await svc.create_task(
            session, title="A", source=TaskSource.TEXT, idempotency_key="tg:1:10"
        )
        second, created_second = await svc.create_task(
            session, title="A (retry)", source=TaskSource.TEXT, idempotency_key="tg:1:10"
        )
        await session.commit()
    assert created_first and not created_second
    assert first.id == second.id
    assert second.title == "A"  # the original row wins


async def test_tasks_without_key_never_collide(session_factory):
    async with session_factory() as session:
        a, _ = await svc.create_task(session, title="same", source=TaskSource.WEB)
        b, _ = await svc.create_task(session, title="same", source=TaskSource.WEB)
        await session.commit()
    assert a.id != b.id


async def test_task_carries_its_author(session_factory):
    async with session_factory() as session:
        user = await upsert_user(session, TelegramSender(telegram_id=42, chat_id=42, first_name="Ann"))
        task, _ = await svc.create_task(session, title="t", source=TaskSource.TEXT, user_id=user.id)
        await session.commit()
    assert task.user is not None and task.user.first_name == "Ann"


async def test_upsert_user_updates_profile_without_duplicating(session_factory):
    async with session_factory() as session:
        first = await upsert_user(session, TelegramSender(7, 7, username="old", first_name="Old"))
        second = await upsert_user(session, TelegramSender(7, 7, username="new", first_name="New"))
        await session.commit()
    assert first.id == second.id
    assert (second.username, second.first_name) == ("new", "New")


async def test_update_status_sets_and_clears_completed_at(session_factory):
    async with session_factory() as session:
        task, _ = await svc.create_task(session, title="t", source=TaskSource.WEB)
        assert await svc.update_status(session, task, TaskStatus.IN_PROGRESS) is True
        assert task.completed_at is None
        assert await svc.update_status(session, task, TaskStatus.COMPLETED) is True
        assert task.completed_at is not None
        assert await svc.update_status(session, task, TaskStatus.COMPLETED) is False  # no-op
        assert await svc.update_status(session, task, TaskStatus.PENDING) is True
        assert task.completed_at is None
        await session.commit()


async def test_list_tasks_newest_first_with_filter_and_total(session_factory):
    async with session_factory() as session:
        for title in ("one", "two", "three"):
            await svc.create_task(session, title=title, source=TaskSource.WEB)
        two = await svc.get_task(session, 2)
        await svc.update_status(session, two, TaskStatus.COMPLETED)
        await session.commit()

    async with session_factory() as session:
        items, total = await svc.list_tasks(session)
        assert [t.title for t in items] == ["three", "two", "one"]
        assert total == 3

        done, done_total = await svc.list_tasks(session, status=TaskStatus.COMPLETED)
        assert [t.title for t in done] == ["two"] and done_total == 1

        page, total = await svc.list_tasks(session, limit=1, offset=1)
        assert [t.title for t in page] == ["two"] and total == 3
