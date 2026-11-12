"""Test startup detection without requiring PostgreSQL or starting service processes."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from arq.constants import health_check_key_suffix

from app.core.queue import QUEUE_NAME
from tests.e2e import conftest as harness


def _stack():
    stack = harness.Stack.__new__(harness.Stack)
    stack.procs = {}
    stack.logs = lambda *args: "service diagnostics"
    return stack


def test_readiness_can_arrive_after_the_old_fifteen_second_limit(monkeypatch):
    clock = [0.0]

    def advance(seconds):
        clock[0] += 5

    monkeypatch.setattr(harness, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=advance))
    stack = _stack()
    assert stack.wait_for(lambda: clock[0] >= 20, "cold worker ready", timeout=90)
    assert clock[0] == 20


def test_failed_process_is_reported_immediately_with_diagnostics():
    stack = _stack()
    stack.procs["worker"] = SimpleNamespace(poll=lambda: 7)
    with pytest.raises(AssertionError, match="worker exited with code 7") as error:
        stack.wait_for(lambda: False, "worker heartbeat", timeout=90, processes=("worker",))
    assert "service diagnostics" in str(error.value)


def test_spawn_clears_stale_heartbeat_before_starting_the_new_worker(tmp_path, monkeypatch):
    stack = _stack()
    stack.commands = {}
    stack.redis = Mock()
    stack.log_dir = tmp_path
    stack.env = {}
    started = Mock()
    monkeypatch.setattr(harness.subprocess, "Popen", started)
    stack._spawn("worker", "arq", "app.worker.settings.WorkerSettings")
    stack.redis.delete.assert_called_once_with(QUEUE_NAME + health_check_key_suffix)
    assert stack.procs["worker"] is started.return_value


def test_worker_readiness_uses_the_heartbeat_rather_than_a_log_message():
    stack = _stack()
    stack.redis = Mock()
    stack.redis.exists.return_value = 0
    assert not stack._worker_ready("worker")
    stack.redis.exists.return_value = 1
    assert stack._worker_ready("worker")
    stack.redis.exists.assert_called_with(QUEUE_NAME + health_check_key_suffix)


def test_timeout_remains_bounded_and_includes_diagnostics(monkeypatch):
    clock = [0.0]

    def advance(seconds):
        clock[0] += seconds

    monkeypatch.setattr(harness, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=advance))
    with pytest.raises(AssertionError, match="timed out waiting for: worker") as error:
        _stack().wait_for(lambda: False, "worker", timeout=0.1)
    assert "service diagnostics" in str(error.value)
    assert clock[0] < 0.2
