"""Tests for _recover_running_tasks preferring status.json."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import scripts.worker as worker


@pytest.fixture
def isolated_task(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.worker.REPO_ROOT", tmp_path)
    (tmp_path / "state" / "tasks").mkdir(parents=True)
    return tmp_path


def _setup_task(tmp_path, task_id: str, status_data: dict | None = None, log_content: str = ""):
    d = tmp_path / "state" / "tasks" / task_id
    d.mkdir(parents=True, exist_ok=True)
    if status_data is not None:
        (d / "status.json").write_text(json.dumps(status_data))
    if log_content:
        (d / "output.log").write_text(log_content)
    return d


def test_status_json_done_preferred(isolated_task, monkeypatch):
    _setup_task(isolated_task, "t1", {"status": "done", "exit_code": 0})
    class MockSession:
        def get(self, *a, **kw):
            return MagicMock(status_code=200, json=lambda: [
                {"id": "t1", "pid": 99999}
            ])
    monkeypatch.setattr("scripts.worker._get_session", lambda: MockSession())
    monkeypatch.setattr("scripts.worker._is_our_process", lambda pid, tid: False)

    reported = []
    monkeypatch.setattr(
        "scripts.worker._report_progress",
        lambda url, tid, status, error_message=None, timeout=5: reported.append((tid, status)),
    )

    worker._recover_running_tasks("http://test:8002")
    assert reported == [("t1", "done")]


def test_status_json_error_preferred(isolated_task, monkeypatch):
    _setup_task(isolated_task, "t2", {"status": "error", "exit_code": 1, "error": "synthetic"})
    class MockSession:
        def get(self, *a, **kw):
            return MagicMock(status_code=200, json=lambda: [
                {"id": "t2", "pid": 99999}
            ])
    monkeypatch.setattr("scripts.worker._get_session", lambda: MockSession())
    monkeypatch.setattr("scripts.worker._is_our_process", lambda pid, tid: False)
    reported = []
    monkeypatch.setattr(
        "scripts.worker._report_progress",
        lambda url, tid, status, error_message=None, timeout=5: reported.append((tid, status, error_message)),
    )

    worker._recover_running_tasks("http://test:8002")
    assert len(reported) == 1
    tid, status, err = reported[0]
    assert status == "error"
    assert "synthetic" in err


def test_no_status_json_falls_back_to_log_marker(isolated_task, monkeypatch):
    _setup_task(
        isolated_task, "t3",
        log_content="some output\n=== Reindex Completed Successfully ===\n",
    )
    class MockSession:
        def get(self, *a, **kw):
            return MagicMock(status_code=200, json=lambda: [
                {"id": "t3", "pid": 99999}
            ])
    monkeypatch.setattr("scripts.worker._get_session", lambda: MockSession())
    monkeypatch.setattr("scripts.worker._is_our_process", lambda pid, tid: False)
    reported = []
    monkeypatch.setattr(
        "scripts.worker._report_progress",
        lambda url, tid, status, error_message=None, timeout=5: reported.append((tid, status)),
    )

    worker._recover_running_tasks("http://test:8002")
    assert reported == [("t3", "done")]


def test_no_markers_interrupted(isolated_task, monkeypatch):
    _setup_task(isolated_task, "t4")
    class MockSession:
        def get(self, *a, **kw):
            return MagicMock(status_code=200, json=lambda: [
                {"id": "t4", "pid": 99999}
            ])
    monkeypatch.setattr("scripts.worker._get_session", lambda: MockSession())
    monkeypatch.setattr("scripts.worker._is_our_process", lambda pid, tid: False)
    reported = []
    monkeypatch.setattr(
        "scripts.worker._report_progress",
        lambda url, tid, status, error_message=None, timeout=5: reported.append((tid, status)),
    )

    worker._recover_running_tasks("http://test:8002")
    assert reported == [("t4", "interrupted")]
