"""Tests for write_task_status helper."""

import json
from quizkb.io_utils import write_task_status


def test_write_status_done(tmp_path):
    write_task_status(tmp_path, "done", 0)
    data = json.loads((tmp_path / "status.json").read_text())
    assert data["status"] == "done"
    assert data["exit_code"] == 0
    assert "finished_at" in data
    assert "error" not in data


def test_write_status_error(tmp_path):
    write_task_status(tmp_path, "error", 1, error="Test error")
    data = json.loads((tmp_path / "status.json").read_text())
    assert data["status"] == "error"
    assert data["exit_code"] == 1
    assert data["error"] == "Test error"


def test_write_status_unwritable(tmp_path):
    """Best-effort — must not raise on missing parent."""
    bad = tmp_path / "does" / "not" / "exist"
    write_task_status(bad, "done", 0)
