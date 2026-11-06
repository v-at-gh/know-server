"""Unit test for worker process_task exception reporting and state reset."""

import pytest
import logging
from unittest.mock import MagicMock
from quizkb.observability import configure_logging
import scripts.worker as worker


def test_worker_process_task_exception_reports_error(monkeypatch):
    """When process_task raises, _execute_task_with_resilience reports error to orchestrator and re-raises."""
    reported_progress = []
    def fake_report(url, task_id, status, error_message=None, timeout=5):
        reported_progress.append((task_id, status, error_message))

    monkeypatch.setattr(worker, "_report_progress", fake_report)

    def failing_process_task(task, args, log, kind):
        raise RuntimeError("synthetic_process_task_failure")

    monkeypatch.setattr(worker, "process_task", failing_process_task)

    fake_args = MagicMock()
    fake_args.orchestrator_url = "http://test-orch:8002"
    fake_log = MagicMock()

    task = {"id": "task-fail-999", "kind": "bootstrap"}

    with pytest.raises(RuntimeError, match="synthetic_process_task_failure"):
        worker._execute_task_with_resilience(task, fake_args, fake_log)

    assert len(reported_progress) == 1
    tid, status, err_msg = reported_progress[0]
    assert tid == "task-fail-999"
    assert status == "error"
    assert "synthetic_process_task_failure" in err_msg
    assert "worker_internal_error" in err_msg


def test_process_task_claimed_logging_does_not_raise(monkeypatch, tmp_path):
    """Regression: task_log.info("task_claimed", ...) with stdlib LoggerAdapter
    must use extra={} rather than structlog-style kwargs.
    """
    log_dir = tmp_path / "logs"
    configure_logging("test_worker_log", log_dir=log_dir)

    monkeypatch.setattr(worker, "_report_progress", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "_spawn_task_process", lambda *a, **kw: None)

    task = {"id": "t1", "action": "bootstrap", "domain": "physics", "lang": "en", "kind": "generation"}

    class _Args:
        orchestrator_url = "http://test:8002"

    logger = logging.getLogger("test_worker_log")

    # Before fix: raised TypeError. After fix: clean return.
    worker.process_task(task, _Args(), logger, "generation")
