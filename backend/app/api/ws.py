import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.deps import token_is_valid

logger = logging.getLogger(__name__)

router = APIRouter()

WS_UNAUTHORIZED = 4401
PONG = json.dumps({"type": "pong"})


@router.websocket("/ws")
async def task_events(websocket: WebSocket) -> None:
    """Push task events to the dashboard.

    Auth uses a `?token=` query parameter because browsers cannot set headers on WebSockets.
    We accept first and then close with a custom code so the client can tell "bad token" apart
    from "network down" (a pre-accept rejection surfaces as a bare 403 in the browser).
    """
    await websocket.accept()
    app = websocket.app
    if not token_is_valid(app.state.settings, websocket.query_params.get("token")):
        await websocket.close(code=WS_UNAUTHORIZED)
        return

    queue = app.state.hub.subscribe()

    async def pump() -> None:
        while True:
            await websocket.send_text(await queue.get())

    sender = asyncio.create_task(pump())
    try:
        while True:
            # The client sends "ping" as a heartbeat; answering through the same queue keeps all
            # sends on one task.
            if (await websocket.receive_text()) == "ping":
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(PONG)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("websocket closed with error", exc_info=True)
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sender
        app.state.hub.unsubscribe(queue)
