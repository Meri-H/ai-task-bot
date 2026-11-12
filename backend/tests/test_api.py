import json

import httpx
import pytest

from app.api.main import create_app
from app.core.config import Settings
from app.core.queue import NOTIFICATION_QUEUE_NAME, QUEUE_NAME
from app.models import TaskSource
from app.services import tasks as task_service
from app.services.events import EVENTS_CHANNEL
from app.services.users import TelegramSender, upsert_user


@pytest.fixture
async def client(settings, redis):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def _make_telegram_task(session_factory) -> int:
    async with session_factory() as session:
        user = await upsert_user(session, TelegramSender(telegram_id=5, chat_id=500, first_name="Ann"))
        task, _ = await task_service.create_task(
            session, title="From Telegram", source=TaskSource.TEXT, user_id=user.id
        )
        await session.commit()
        return task.id


async def test_health(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "redis": "ok"}


async def test_create_and_list_tasks(client):
    created = await client.post("/api/tasks", json={"title": "  Write tests  "})
    assert created.status_code == 201
    body = created.json()
    assert body["title"] == "Write tests"
    assert body["status"] == "pending" and body["source"] == "web" and body["user"] is None

    listing = (await client.get("/api/tasks")).json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == body["id"]


@pytest.mark.parametrize("payload", [{"title": "   "}, {}, {"title": "x" * 201}])
async def test_create_task_validation(client, payload):
    assert (await client.post("/api/tasks", json=payload)).status_code == 422


async def test_list_filters_by_status(client):
    a = (await client.post("/api/tasks", json={"title": "a"})).json()
    await client.post("/api/tasks", json={"title": "b"})
    await client.patch(f"/api/tasks/{a['id']}", json={"status": "completed"})

    done = (await client.get("/api/tasks", params={"status": "completed"})).json()
    assert [t["title"] for t in done["items"]] == ["a"]
    assert (await client.get("/api/tasks", params={"status": "bogus"})).status_code == 422


async def test_patch_status_roundtrip(client):
    task = (await client.post("/api/tasks", json={"title": "t"})).json()
    for new in ("in_progress", "completed", "pending"):
        response = await client.patch(f"/api/tasks/{task['id']}", json={"status": new})
        assert response.status_code == 200
        assert response.json()["status"] == new
    assert (await client.get(f"/api/tasks/{task['id']}")).json()["status"] == "pending"


async def test_patch_validation_and_not_found(client):
    task = (await client.post("/api/tasks", json={"title": "t"})).json()
    assert (await client.patch(f"/api/tasks/{task['id']}", json={"status": "done"})).status_code == 422
    assert (await client.patch("/api/tasks/9999", json={"status": "completed"})).status_code == 404
    assert (await client.get("/api/tasks/9999")).status_code == 404


async def test_changes_are_published_as_realtime_events(client, redis):
    pubsub = redis.pubsub()
    await pubsub.subscribe(EVENTS_CHANNEL)
    await pubsub.get_message(timeout=1)  # subscription confirmation

    task = (await client.post("/api/tasks", json={"title": "live"})).json()
    created = json.loads((await pubsub.get_message(ignore_subscribe_messages=True, timeout=2))["data"])
    assert created["type"] == "task.created" and created["task"]["id"] == task["id"]

    await client.patch(f"/api/tasks/{task['id']}", json={"status": "in_progress"})
    updated = json.loads((await pubsub.get_message(ignore_subscribe_messages=True, timeout=2))["data"])
    assert updated["type"] == "task.updated" and updated["task"]["status"] == "in_progress"
    await pubsub.aclose()


async def test_no_event_when_status_does_not_change(client, redis):
    task = (await client.post("/api/tasks", json={"title": "t"})).json()
    pubsub = redis.pubsub()
    await pubsub.subscribe(EVENTS_CHANNEL)
    await pubsub.get_message(timeout=1)

    await client.patch(f"/api/tasks/{task['id']}", json={"status": "pending"})  # already pending
    assert await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.3) is None
    await pubsub.aclose()


async def test_status_change_queues_telegram_notification_only_for_telegram_tasks(
    client, redis, session_factory
):
    web = (await client.post("/api/tasks", json={"title": "web task"})).json()
    await client.patch(f"/api/tasks/{web['id']}", json={"status": "completed"})
    assert await redis.zcard(NOTIFICATION_QUEUE_NAME) == 0  # no Telegram author -> nobody to tell

    tg_id = await _make_telegram_task(session_factory)
    await client.patch(f"/api/tasks/{tg_id}", json={"status": "in_progress"})
    assert await redis.zcard(NOTIFICATION_QUEUE_NAME) == 1
    assert await redis.zcard(QUEUE_NAME) == 0


async def test_tasks_from_telegram_expose_author(client, session_factory):
    await _make_telegram_task(session_factory)
    item = (await client.get("/api/tasks")).json()["items"][0]
    assert item["source"] == "text"
    assert item["user"] == {"id": 1, "username": None, "first_name": "Ann", "display_name": "Ann"}


# --- auth ------------------------------------------------------------------------------------


@pytest.fixture
async def secured_client(settings, redis):
    secured = Settings(**{**settings.model_dump(), "dashboard_token": "s3cret"})
    app = create_app(secured)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic s3cret"}, {"Authorization": "s3cret"}],
)
async def test_api_requires_token_when_configured(secured_client, headers):
    assert (await secured_client.get("/api/tasks", headers=headers)).status_code == 401
    assert (await secured_client.post("/api/tasks", json={"title": "x"}, headers=headers)).status_code == 401


async def test_api_accepts_correct_token_and_health_stays_open(secured_client):
    ok = await secured_client.get("/api/tasks", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
    assert (await secured_client.get("/api/health")).status_code == 200


async def test_delete_task_removes_it_and_publishes_event(client, redis):
    task = (await client.post("/api/tasks", json={"title": "Remove me"})).json()
    pubsub = redis.pubsub()
    await pubsub.subscribe(EVENTS_CHANNEL)
    await pubsub.get_message(timeout=1)
    try:
        response = await client.delete(f"/api/tasks/{task['id']}")
        assert response.status_code == 204
        assert response.content == b""
        event = json.loads((await pubsub.get_message(ignore_subscribe_messages=True, timeout=2))["data"])
        assert event == {"type": "task.deleted", "task_id": task["id"]}
        assert (await client.get(f"/api/tasks/{task['id']}")).status_code == 404
        assert (await client.get("/api/tasks")).json() == {"items": [], "total": 0}
        assert (await client.delete(f"/api/tasks/{task['id']}")).status_code == 404
    finally:
        await pubsub.aclose()


async def test_delete_requires_auth(secured_client):
    assert (await secured_client.delete("/api/tasks/1")).status_code == 401
    response = await secured_client.delete(
        "/api/tasks/9999", headers={"Authorization": "Bearer s3cret"}
    )
    assert response.status_code == 404


async def test_delete_voice_task_preserves_job_history(client, session_factory):
    from sqlalchemy import select

    from app.models import TranscriptionJob

    task_id = await _make_telegram_task(session_factory)
    async with session_factory() as session:
        job = TranscriptionJob(
            chat_id=500, message_id=100, file_id="voice-file", status="succeeded", task_id=task_id
        )
        session.add(job)
        await session.commit()
        job_id = job.id
    assert (await client.delete(f"/api/tasks/{task_id}")).status_code == 204
    async with session_factory() as session:
        result = await session.execute(select(TranscriptionJob).where(TranscriptionJob.id == job_id))
        job = result.scalar_one()
        assert job.task_id is None
        assert job.status == "succeeded"


async def test_cursor_pages_survive_inserts_and_deletes(client):
    tasks = []
    for title in ("one", "two", "three", "four"):
        tasks.append((await client.post("/api/tasks", json={"title": title})).json())
    page = (await client.get("/api/tasks", params={"limit": 2})).json()
    assert [t["title"] for t in page["items"]] == ["four", "three"]
    cursor = page["items"][-1]
    # Offset pagination would skip "two" after this deletion. The cursor does not shift.
    await client.delete(f"/api/tasks/{tasks[3]['id']}")
    await client.post("/api/tasks", json={"title": "five"})
    older = (await client.get("/api/tasks", params={
        "limit": 2, "before_id": cursor["id"], "before_created_at": cursor["created_at"],
    })).json()
    assert [t["title"] for t in older["items"]] == ["two", "one"]
    assert older["total"] == 4


@pytest.mark.parametrize("params", [
    {"before_id": 1},
    {"before_created_at": "2026-01-01T00:00:00Z"},
    {"before_id": 1, "before_created_at": "2026-01-01T00:00:00"},
    {"before_id": 1, "before_created_at": "2026-01-01T00:00:00Z", "offset": 1},
])
async def test_invalid_cursor_is_rejected(client, params):
    assert (await client.get("/api/tasks", params=params)).status_code == 422
