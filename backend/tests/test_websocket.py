"""WebSocket tests use Starlette's sync TestClient, which runs the whole app (including the Redis
subscriber task) on its own event loop, exactly like a real server process."""

import asyncio

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.hub import CLIENT_QUEUE_SIZE, RESYNC_MESSAGE, EventHub
from app.api.main import create_app
from app.core.config import Settings

pytestmark = pytest.mark.timeout(30)


def test_dashboard_receives_events_for_new_and_updated_tasks(settings, clean_db_sync):
    with TestClient(create_app(settings)) as client, client.websocket_connect("/api/ws") as ws:
        task = client.post("/api/tasks", json={"title": "Buy milk"}).json()
        created = ws.receive_json()
        assert created["type"] == "task.created"
        assert created["task"]["title"] == "Buy milk"

        client.patch(f"/api/tasks/{task['id']}", json={"status": "completed"})
        updated = ws.receive_json()
        assert updated["type"] == "task.updated"
        assert updated["task"]["status"] == "completed"
        assert updated["task"]["completed_at"] is not None


def test_every_connected_dashboard_gets_every_event(settings, clean_db_sync):
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/api/ws") as a, client.websocket_connect("/api/ws") as b:
            task = client.post("/api/tasks", json={"title": "shared"}).json()
            assert a.receive_json()["task"]["title"] == "shared"
            assert b.receive_json()["task"]["title"] == "shared"
            assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
            expected = {"type": "task.deleted", "task_id": task["id"]}
            assert a.receive_json() == expected
            assert b.receive_json() == expected


def test_heartbeat(settings, clean_db_sync):
    with TestClient(create_app(settings)) as client, client.websocket_connect("/api/ws") as ws:
        ws.send_text("ping")
        assert ws.receive_json() == {"type": "pong"}


def test_websocket_rejects_missing_or_wrong_token(settings, clean_db_sync):
    secured = Settings(**{**settings.model_dump(), "dashboard_token": "s3cret"})
    with TestClient(create_app(secured)) as client:
        for url in ("/api/ws", "/api/ws?token=nope"):
            with client.websocket_connect(url) as ws, pytest.raises(WebSocketDisconnect) as info:
                ws.receive_json()
            assert info.value.code == 4401

        with client.websocket_connect("/api/ws?token=s3cret") as ws:
            ws.send_text("ping")
            assert ws.receive_json() == {"type": "pong"}


async def test_slow_client_gets_resync_instead_of_blocking_others():
    hub = EventHub(redis=None)  # type: ignore[arg-type]  # fan-out logic only
    slow, healthy = hub.subscribe(), hub.subscribe()

    for i in range(CLIENT_QUEUE_SIZE + 1):
        hub._broadcast(f"event-{i}")
        if i < CLIENT_QUEUE_SIZE:
            healthy.get_nowait()  # a healthy client keeps up

    assert slow.qsize() == 1 and slow.get_nowait() == RESYNC_MESSAGE
    assert healthy.get_nowait() == f"event-{CLIENT_QUEUE_SIZE}"
    assert hub.client_count == 2
    hub.unsubscribe(slow)
    assert hub.client_count == 1
    assert isinstance(slow, asyncio.Queue)
