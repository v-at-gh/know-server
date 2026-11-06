"""Tests for TaskManager.reconcile_orphaned_tasks."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
import psutil

from orchestrator.tasks import TaskManager


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Isolated tasks.db + TASKS_DIR."""
    monkeypatch.setattr("orchestrator.tasks.DB_PATH", tmp_path / "tasks.db")
    monkeypatch.setattr("orchestrator.tasks.TASKS_DIR", tmp_path / "tasks")
    (tmp_path / "tasks").mkdir()
    tm = TaskManager()
    return tm, tmp_path


def _enqueue_running(tm, task_id: str, pid: int | None, started_at: str | None = None):
    if started_at is None:
        started_at = datetime.now().isoformat()
    with tm._get_conn() as conn:
        conn.execute(
            "INSERT INTO tasks (id, action, domain, lang, status, pid, started_at, kind) "
            "VALUES (?, 'bootstrap', 'x', 'en', 'running', ?, ?, 'generation')",
            (task_id, pid, started_at),
        )
        conn.commit()


def _write_task_dir(tmp_path, task_id: str, status_data: dict | None = None):
    d = tmp_path / "tasks" / task_id
    d.mkdir(parents=True, exist_ok=True)
    if status_data is not None:
        (d / "status.json").write_text(json.dumps(status_data))
    return d


def _mock_no_process(monkeypatch):
    def _raise(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, "Process", _raise)


def _mock_alive_matching(monkeypatch, task_id: str):
    def _proc(pid):
        m = MagicMock()
        m.status.return_value = psutil.STATUS_RUNNING
        m.cmdline.return_value = ["python", "scripts/generate_content.py", "--task-id", task_id]
        return m
    monkeypatch.setattr(psutil, "Process", _proc)


def _mock_alive_mismatch(monkeypatch):
    def _proc(pid):
        m = MagicMock()
        m.status.return_value = psutil.STATUS_RUNNING
        m.cmdline.return_value = ["python", "some_other_script.py"]
        return m
    monkeypatch.setattr(psutil, "Process", _proc)


def test_dead_pid_with_done_marker(isolated, monkeypatch):
    tm, tmp_path = isolated
    _enqueue_running(tm, "t1", pid=99999)
    _write_task_dir(tmp_path, "t1", {"status": "done", "exit_code": 0, "finished_at": "x"})
    _mock_no_process(monkeypatch)

    result = tm.reconcile_orphaned_tasks()
    assert result["reconciled"] == 1

    task = tm.get_task("t1")
    assert task["status"] == "done"


def test_dead_pid_with_error_marker(isolated, monkeypatch):
    tm, tmp_path = isolated
    _enqueue_running(tm, "t2", pid=99999)
    _write_task_dir(tmp_path, "t2", {"status": "error", "exit_code": 1, "error": "synthetic failure"})
    _mock_no_process(monkeypatch)

    result = tm.reconcile_orphaned_tasks()
    assert result["reconciled"] == 1

    task = tm.get_task("t2")
    assert task["status"] == "error"
    assert "synthetic failure" in task["error_message"]


def test_dead_pid_no_marker(isolated, monkeypatch):
    tm, tmp_path = isolated
    _enqueue_running(tm, "t3", pid=99999)
    _write_task_dir(tmp_path, "t3")
    _mock_no_process(monkeypatch)

    result = tm.reconcile_orphaned_tasks()
    assert result["reconciled"] == 1

    task = tm.get_task("t3")
    assert task["status"] == "interrupted"
    assert "no marker" in task["error_message"]


def test_alive_pid_matching_skipped(isolated, monkeypatch):
    tm, tmp_path = isolated
    _enqueue_running(tm, "t4", pid=12345)
    _write_task_dir(tmp_path, "t4", {"status": "done", "exit_code": 0})
    _mock_alive_matching(monkeypatch, "t4")

    result = tm.reconcile_orphaned_tasks()
    assert result["skipped"] == 1
    assert result["reconciled"] == 0

    task = tm.get_task("t4")
    assert task["status"] == "running"


def test_alive_pid_mismatch_reconciles(isolated, monkeypatch):
    tm, tmp_path = isolated
    _enqueue_running(tm, "t5", pid=12345)
    _write_task_dir(tmp_path, "t5", {"status": "done", "exit_code": 0})
    _mock_alive_mismatch(monkeypatch)

    result = tm.reconcile_orphaned_tasks()
    assert result["reconciled"] == 1
    assert tm.get_task("t5")["status"] == "done"


def test_null_pid_recent_skipped(isolated, monkeypatch):
    tm, tmp_path = isolated
    _enqueue_running(tm, "t6", pid=None, started_at=datetime.now().isoformat())
    result = tm.reconcile_orphaned_tasks()
    assert result["skipped"] == 1
    assert tm.get_task("t6")["status"] == "running"


def test_null_pid_old_with_scan_hit_skipped(isolated, monkeypatch):
    tm, tmp_path = isolated
    old = (datetime.now() - timedelta(minutes=5)).isoformat()
    _enqueue_running(tm, "t7", pid=None, started_at=old)

    monkeypatch.setattr(
        "orchestrator.tasks.TaskManager._scan_for_task_id",
        lambda self, tid: True,
    )

    result = tm.reconcile_orphaned_tasks()
    assert result["skipped"] == 1
    assert tm.get_task("t7")["status"] == "running"


def test_null_pid_old_no_scan_marker_error(isolated, monkeypatch):
    tm, tmp_path = isolated
    old = (datetime.now() - timedelta(minutes=5)).isoformat()
    _enqueue_running(tm, "t8", pid=None, started_at=old)
    _write_task_dir(tmp_path, "t8")

    monkeypatch.setattr(
        "orchestrator.tasks.TaskManager._scan_for_task_id",
        lambda self, tid: False,
    )

    result = tm.reconcile_orphaned_tasks()
    assert result["reconciled"] == 1
    task = tm.get_task("t8")
    assert task["status"] == "interrupted"
    assert "worker_died_before_spawn" in task["error_message"]


def test_access_denied_skipped(isolated, monkeypatch):
    tm, tmp_path = isolated
    _enqueue_running(tm, "t9", pid=12345)
    _write_task_dir(tmp_path, "t9", {"status": "done"})

    def _raise(pid):
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(psutil, "Process", _raise)

    result = tm.reconcile_orphaned_tasks()
    assert result["skipped"] == 1
    assert tm.get_task("t9")["status"] == "running"


def test_reindex_marker_fallback(isolated, monkeypatch):
    tm, tmp_path = isolated
    _enqueue_running(tm, "t10", pid=99999)
    task_dir = _write_task_dir(tmp_path, "t10")
    (task_dir / "output.log").write_text(
        "some output\n=== Reindex Completed Successfully ===\n"
    )
    _mock_no_process(monkeypatch)

    result = tm.reconcile_orphaned_tasks()
    assert result["reconciled"] == 1
    assert tm.get_task("t10")["status"] == "done"
