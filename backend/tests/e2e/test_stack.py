"""End-to-end: real API + bot + worker processes, real Postgres + Redis, fake Telegram/OpenAI.

What this proves: the processes are wired together correctly (HTTP, Redis queue, pub/sub,
WebSocket, DB). What it does NOT prove: behaviour against the real Telegram and OpenAI services.
"""

import json
import time

import pytest
from websockets.sync.client import connect

from app import messages

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(180)]


@pytest.fixture
def dashboard(stack):
    """A connected dashboard (WebSocket client)."""
    with connect(f"ws://127.0.0.1:{stack.api_port}/api/ws") as ws:
        yield ws


def next_event(ws, timeout: float = 10):
    return json.loads(ws.recv(timeout=timeout))


def test_text_message_becomes_task_and_shows_up_live(stack, dashboard):
    stack.send_text("Buy oat milk", message_id=101)

    event = next_event(dashboard)
    assert event["type"] == "task.created"
    assert event["task"]["title"] == "Buy oat milk"
    assert event["task"]["source"] == "text"
    assert event["task"]["user"]["first_name"] == "Ann"

    reply = stack.wait_for(stack.bot_messages, "bot reply")[0]
    assert reply["text"] == messages.task_created(event["task"]["id"], "Buy oat milk")
    assert [t["title"] for t in stack.tasks()] == ["Buy oat milk"]


def test_voice_message_is_transcribed_through_the_queue(stack, dashboard):
    pushed = stack.send_voice("Call the dentist tomorrow morning", message_id=201)

    # 1) Instant acknowledgement (before any transcription work).
    ack = stack.wait_for(stack.bot_messages, "acknowledgement")[0]
    assert ack["text"] == messages.TRANSCRIBING

    # 2) The same message is edited into the result by the worker.
    edit = stack.wait_for(stack.bot_edits, "result edit")[0]
    assert edit["message_id"] == ack["message_id"]
    assert "Call the dentist tomorrow morning" in edit["text"]

    # 3) The dashboard got a live event for it.
    event = next_event(dashboard)
    assert (event["type"], event["task"]["source"]) == ("task.created", "voice")
    assert event["task"]["title"] == "Call the dentist tomorrow morning"

    # 4) The durable job record.
    (job,) = stack.sql("SELECT status, attempts, transcript, language, processing_ms FROM transcription_jobs")
    assert (job["status"], job["attempts"], job["language"]) == ("succeeded", 1, "english")
    assert job["transcript"] == "Call the dentist tomorrow morning"

    ack_ms = (ack["at"] - pushed["pushed_at"]) * 1000
    total_ms = (edit["at"] - pushed["pushed_at"]) * 1000
    print(f"\n[e2e timing, fake Telegram/OpenAI on localhost] ack={ack_ms:.0f}ms result={total_ms:.0f}ms")
    assert ack["at"] <= edit["at"]


def test_dashboard_status_change_notifies_the_telegram_author(stack, dashboard):
    stack.send_text("Ship the release", message_id=301)
    task = next_event(dashboard)["task"]
    stack.wait_for(stack.bot_messages, "task reply")

    response = stack.http.patch(f"{stack.api}/api/tasks/{task['id']}", json={"status": "completed"})
    assert response.status_code == 200

    updated = next_event(dashboard)
    assert (updated["type"], updated["task"]["status"]) == ("task.updated", "completed")

    expected = messages.status_changed(task["id"], "Ship the release", "completed")
    stack.wait_for(lambda: any(m["text"] == expected for m in stack.bot_messages()), "status notification")


def test_transient_provider_errors_are_retried_transparently(stack, dashboard):
    stack.fail_openai(503, 429)  # first two calls fail, third succeeds
    stack.send_voice("Water the plants", message_id=401)

    event = next_event(dashboard, timeout=20)
    assert event["task"]["title"] == "Water the plants"
    (job,) = stack.sql("SELECT status, attempts FROM transcription_jobs")
    assert (job["status"], job["attempts"]) == ("succeeded", 3)
    assert stack.fake_state()["openai_calls"] == 3
    assert len(stack.tasks()) == 1
    # The user never saw an error message, only "Transcribing…" -> result.
    assert [e["text"] for e in stack.bot_edits()] == [
        messages.voice_task_created(event["task"]["id"], "Water the plants")
    ]


def test_outage_longer_than_retry_budget_fails_cleanly(stack):
    stack.fail_openai(always=503)
    stack.send_voice("Pay the invoice", message_id=501)

    stack.wait_for(stack.bot_edits, "failure message", timeout=20)
    assert stack.bot_edits()[0]["text"] == messages.VOICE_FAILURES["unavailable"]
    (job,) = stack.sql("SELECT status, attempts, error FROM transcription_jobs")
    assert (job["status"], job["attempts"]) == ("failed", 3)
    assert stack.tasks() == []
    assert stack.fake_state()["openai_calls"] == 3


@pytest.mark.parametrize(
    ("content", "message_id"),
    [("CORRUPT", 601), ("SILENCE", 602)],
)
def test_unusable_audio_gets_a_helpful_reply_without_retries(stack, content, message_id):
    stack.send_voice(content, message_id=message_id)

    stack.wait_for(stack.bot_edits, "failure message")
    assert stack.bot_edits()[0]["text"] == messages.VOICE_FAILURES["invalid_audio"]
    (job,) = stack.sql("SELECT status, attempts FROM transcription_jobs")
    assert (job["status"], job["attempts"]) == ("failed", 1)  # permanent: no retry
    assert stack.tasks() == []


def test_too_long_voice_is_rejected_before_it_touches_the_queue(stack):
    stack.send_voice("irrelevant", message_id=701, duration=600)

    reply = stack.wait_for(stack.bot_messages, "rejection")[0]
    assert reply["text"] == messages.voice_too_long(60)
    assert stack.sql("SELECT count(*) AS n FROM transcription_jobs")[0]["n"] == 0
    assert stack.fake_state()["openai_calls"] == 0


def test_telegram_redelivering_an_update_does_not_duplicate_the_task(stack, dashboard):
    stack.send_text("Only once", message_id=801)
    stack.send_text("Only once", message_id=801)  # same message, new update id
    next_event(dashboard)
    time.sleep(1.0)  # give a (wrongly) created duplicate time to appear
    assert [t["title"] for t in stack.tasks()] == ["Only once"]


def test_unsupported_content_and_help(stack):
    stack.send_text("/help", message_id=901)
    reply = stack.wait_for(stack.bot_messages, "help reply")[0]
    assert reply["text"] == messages.HELP
    assert stack.tasks() == []  # commands never become tasks


def test_two_dashboards_share_creation_status_and_deletion(stack):
    url = f"ws://127.0.0.1:{stack.api_port}/api/ws"
    with connect(url) as first, connect(url) as second:
        stack.send_text("Shared task", message_id=1001)
        task = next_event(first)["task"]
        assert next_event(second)["task"]["id"] == task["id"]
        assert stack.http.patch(
            f"{stack.api}/api/tasks/{task['id']}", json={"status": "completed"}
        ).status_code == 200
        assert next_event(first)["task"]["status"] == "completed"
        assert next_event(second)["task"]["status"] == "completed"
        assert stack.http.delete(f"{stack.api}/api/tasks/{task['id']}").status_code == 204
        expected = {"type": "task.deleted", "task_id": task["id"]}
        assert next_event(first) == expected
        assert next_event(second) == expected


def test_tasks_persist_and_notifications_resume_after_service_restart(stack):
    stack.send_voice("Persist after restart", message_id=1101)
    tasks = stack.wait_for(stack.tasks, "voice task saved")
    task_id = tasks[0]["id"]
    stack.wait_for(stack.bot_edits, "voice acknowledgement resolved")
    stack.restart_service("api")
    stack.restart_service("worker")
    assert [(t["id"], t["title"]) for t in stack.tasks()] == [(task_id, "Persist after restart")]
    assert stack.http.patch(
        f"{stack.api}/api/tasks/{task_id}", json={"status": "completed"}
    ).status_code == 200
    expected = messages.status_changed(task_id, "Persist after restart", "completed")
    stack.wait_for(
        lambda: any(m["text"] == expected for m in stack.bot_messages()), "notification after restart"
    )


def test_voice_waits_in_queue_while_worker_is_stopped(stack):
    stack.stop_service("worker")
    try:
        stack.send_voice("Process me after restart", message_id=1201)
        stack.wait_for(stack.bot_messages, "voice acknowledgement")
        jobs = stack.wait_for(lambda: stack.sql("SELECT status FROM transcription_jobs"), "job persisted")
        assert jobs[0]["status"] == "queued"
        assert stack.tasks() == []
    finally:
        stack.restart_service("worker")
    tasks = stack.wait_for(stack.tasks, "queued voice processed")
    assert tasks[0]["title"] == "Process me after restart"


def test_duplicate_voice_reply_shows_saved_outcome(stack):
    stack.send_voice("Do this once", message_id=1301)
    stack.wait_for(stack.bot_edits, "first voice result")
    task_id = stack.tasks()[0]["id"]
    stack.send_voice("Do this once", message_id=1301)
    expected = messages.duplicate_voice("succeeded", task_id)
    stack.wait_for(lambda: any(e["text"] == expected for e in stack.bot_edits()), "duplicate outcome")
    assert len(stack.tasks()) == 1
    assert stack.fake_state()["openai_calls"] == 1


def test_status_notification_does_not_wait_for_the_voice_worker(stack):
    stack.send_text("Notify independently", message_id=1401)
    task = stack.wait_for(stack.tasks, "text task saved")[0]
    stack.wait_for(stack.bot_messages, "text acknowledgement")
    stack.stop_service("worker")
    try:
        stack.send_voice("Voice can wait", message_id=1402)
        stack.wait_for(lambda: stack.sql("SELECT status FROM transcription_jobs"), "queued voice")
        assert stack.http.patch(
            f"{stack.api}/api/tasks/{task['id']}", json={"status": "in_progress"}
        ).status_code == 200
        expected = messages.status_changed(task["id"], "Notify independently", "in_progress")
        stack.wait_for(
            lambda: any(m["text"] == expected for m in stack.bot_messages()),
            "independent status notification",
        )
        assert stack.sql("SELECT status FROM transcription_jobs")[0]["status"] == "queued"
    finally:
        stack.restart_service("worker")
    stack.wait_for(lambda: len(stack.tasks()) == 2, "voice processed after its worker restarts")
