"""A fake Telegram Bot API + fake OpenAI transcription endpoint in one small aiohttp server.

Used by the e2e tests so the *real* bot, worker and API processes can be exercised without any
credentials or internet access:

  * Telegram:  getMe, getUpdates (long polling), sendMessage, editMessageText, getFile, file
               download, setMyCommands, deleteWebhook.
  * OpenAI:    POST /v1/audio/transcriptions. The "audio" file's bytes ARE the transcript, which
               lets a test prove the voice file really travelled Telegram -> worker -> provider.
               Special contents: b"SILENCE" -> empty transcript, b"CORRUPT" -> HTTP 400.
  * Control:   /_control/* endpoints the tests use to inject updates and failures.

Run:  python -m tests.e2e.fake_services --port 8765
"""

import argparse
import asyncio
import json
import time
from typing import Any

from aiohttp import web


class State:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.bot_polling = getattr(self, "bot_polling", False)
        # Update ids are monotonic across resets, like real Telegram: the bot's polling offset
        # only ever moves forward, so recycled ids would be silently skipped.
        self.next_update_id = getattr(self, "next_update_id", 1)
        self.next_message_id = getattr(self, "next_message_id", 5000)
        self.sent: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.files: dict[str, bytes] = {}
        self.pushed_at: dict[int, float] = {}  # message_id -> wall clock when the update was queued
        self.openai_calls = 0
        self.fail_statuses: list[int] = []
        self.fail_always: int | None = None
        self.telegram_fail_statuses: list[int] = []


STATE = State()


def ok(result: Any) -> web.Response:
    return web.json_response({"ok": True, "result": result})


def _message(chat_id: int, text: str, message_id: int) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "date": int(time.time()),
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": 123456, "is_bot": True, "first_name": "Fake bot", "username": "fake_bot"},
        "text": text,
    }


# --- Telegram -------------------------------------------------------------------------------


async def telegram_method(request: web.Request) -> web.Response:
    method = request.match_info["method"]
    params: dict[str, Any] = dict(await request.post()) if request.can_read_body else {}

    if method == "getMe":
        return ok({"id": 123456, "is_bot": True, "first_name": "Fake bot", "username": "fake_bot"})
    if method in ("setMyCommands", "deleteWebhook"):
        return ok(True)

    if method == "getUpdates":
        STATE.bot_polling = True
        offset = int(params.get("offset") or 0)
        deadline = time.monotonic() + min(int(params.get("timeout") or 0), 1)
        STATE.updates = [u for u in STATE.updates if u["update_id"] >= offset]
        while True:
            pending = [u for u in STATE.updates if u["update_id"] >= offset]
            if pending or time.monotonic() >= deadline:
                return ok(pending)
            await asyncio.sleep(0.02)

    if STATE.telegram_fail_statuses and method in ("sendMessage", "editMessageText", "getFile"):
        status = STATE.telegram_fail_statuses.pop(0)
        return web.json_response(
            {"ok": False, "error_code": status, "description": "injected"}, status=status
        )

    if method == "sendMessage":
        message_id = STATE.next_message_id
        STATE.next_message_id += 1
        STATE.sent.append(
            {
                "message_id": message_id,
                "chat_id": int(params["chat_id"]),
                "text": params["text"],
                "at": time.time(),
            }
        )
        return ok(_message(int(params["chat_id"]), params["text"], message_id))

    if method == "editMessageText":
        STATE.edits.append(
            {
                "message_id": int(params["message_id"]),
                "chat_id": int(params["chat_id"]),
                "text": params["text"],
                "at": time.time(),
            }
        )
        return ok(_message(int(params["chat_id"]), params["text"], int(params["message_id"])))

    if method == "getFile":
        file_id = params["file_id"]
        if file_id not in STATE.files:
            return web.json_response(
                {"ok": False, "error_code": 400, "description": "Bad Request: wrong file_id"}, status=400
            )
        return ok(
            {
                "file_id": file_id,
                "file_unique_id": f"uniq-{file_id}",
                "file_size": len(STATE.files[file_id]),
                "file_path": f"voice/{file_id}.oga",
            }
        )

    return web.json_response(
        {"ok": False, "error_code": 404, "description": f"unknown method {method}"}, status=404
    )


async def telegram_file(request: web.Request) -> web.Response:
    file_id = request.match_info["path"].removeprefix("voice/").removesuffix(".oga")
    if file_id not in STATE.files:
        return web.Response(status=404)
    return web.Response(body=STATE.files[file_id], content_type="audio/ogg")


# --- OpenAI ---------------------------------------------------------------------------------


async def openai_transcriptions(request: web.Request) -> web.Response:
    STATE.openai_calls += 1
    if request.headers.get("Authorization") != "Bearer sk-e2e":
        return web.json_response({"error": {"message": "bad key", "code": "invalid_api_key"}}, status=401)

    status = STATE.fail_always or (STATE.fail_statuses.pop(0) if STATE.fail_statuses else None)
    if status:
        return web.json_response(
            {"error": {"message": "injected failure", "code": "server_error"}}, status=status
        )

    form = await request.post()
    content: bytes = form["file"].file.read()
    if content == b"CORRUPT":
        return web.json_response({"error": {"message": "Invalid file format.", "code": None}}, status=400)
    text = "" if content == b"SILENCE" else content.decode()
    return web.json_response({"text": text, "language": "english", "duration": 2.0})


# --- Test control ---------------------------------------------------------------------------


async def control_reset(request: web.Request) -> web.Response:
    STATE.reset()
    return web.json_response({"ok": True})


async def control_user_update(request: web.Request) -> web.Response:
    """Queue a user message (kind: text or voice; see the fields read below)."""
    body = await request.json()
    update_id = STATE.next_update_id
    STATE.next_update_id += 1
    message: dict[str, Any] = {
        "message_id": body["message_id"],
        "date": int(time.time()),
        "chat": {"id": body["chat_id"], "type": "private", "first_name": body.get("first_name", "Ann")},
        "from": {
            "id": body["user_id"],
            "is_bot": False,
            "first_name": body.get("first_name", "Ann"),
            "username": body.get("username", "ann"),
        },
    }
    if body["kind"] == "text":
        message["text"] = body["text"]
    else:
        content = body["content"].encode()
        STATE.files[body["file_id"]] = content
        message["voice"] = {
            "file_id": body["file_id"],
            "file_unique_id": f"uniq-{body['file_id']}",
            "duration": body.get("duration", 3),
            "mime_type": "audio/ogg",
            "file_size": len(content),
        }
    STATE.pushed_at[body["message_id"]] = time.time()
    STATE.updates.append({"update_id": update_id, "message": message})
    return web.json_response({"update_id": update_id, "pushed_at": STATE.pushed_at[body["message_id"]]})


async def control_openai_failures(request: web.Request) -> web.Response:
    body = await request.json()
    STATE.fail_statuses = list(body.get("statuses", []))
    STATE.fail_always = body.get("always")
    return web.json_response({"ok": True})


async def control_state(request: web.Request) -> web.Response:
    return web.Response(
        text=json.dumps(
            {
                "sent": STATE.sent,
                "edits": STATE.edits,
                "openai_calls": STATE.openai_calls,
                "pushed_at": {str(k): v for k, v in STATE.pushed_at.items()},
            }
        ),
        content_type="application/json",
    )


def make_app() -> web.Application:
    app = web.Application(client_max_size=50 * 1024 * 1024)
    app.router.add_post("/bot{token}/{method}", telegram_method)
    app.router.add_get("/file/bot{token}/{path:.+}", telegram_file)
    app.router.add_post("/v1/audio/transcriptions", openai_transcriptions)
    app.router.add_post("/_control/reset", control_reset)
    app.router.add_post("/_control/user_update", control_user_update)
    app.router.add_post("/_control/openai_failures", control_openai_failures)
    app.router.add_get("/_control/state", control_state)
    app.router.add_get(
        "/_control/health", lambda request: web.json_response({"ok": True, "bot_polling": STATE.bot_polling})
    )
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    web.run_app(make_app(), host="127.0.0.1", port=args.port, print=None)
