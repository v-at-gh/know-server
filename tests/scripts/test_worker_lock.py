"""Worker lock: refuse second worker, --allow-duplicate bypass."""

import subprocess
import sys
import time

import pytest


def test_second_worker_refused(tmp_path, monkeypatch):
    from quizkb.lock import acquire
    monkeypatch.setenv("KNOW_STATE_DIR", str(tmp_path / "state"))
    from quizkb.settings import reset_settings_cache
    reset_settings_cache()

    lock = acquire("know-worker",
                   metadata_dir=tmp_path / "state" / "locks",
                   command="test-holder")
    try:
        result = subprocess.run(
            [sys.executable, "scripts/worker.py",
             "--orchestrator-url", "http://127.0.0.1:9999"],
            capture_output=True, text=True, timeout=15,
            env={**__import__("os").environ,
                 "KNOW_STATE_DIR": str(tmp_path / "state")},
        )
        assert result.returncode == 1
        assert "Refusing to start" in result.stderr or "Another worker is running" in result.stderr
    finally:
        lock.release()


def test_allow_duplicate_starts(tmp_path, monkeypatch):
    """--allow-duplicate should bypass lock acquisition."""
    from quizkb.lock import acquire
    monkeypatch.setenv("KNOW_STATE_DIR", str(tmp_path / "state"))
    from quizkb.settings import reset_settings_cache
    reset_settings_cache()

    lock = acquire("know-worker",
                   metadata_dir=tmp_path / "state" / "locks",
                   command="test-holder")
    try:
        p = subprocess.Popen(
            [sys.executable, "scripts/worker.py",
             "--orchestrator-url", "http://127.0.0.1:9999",
             "--allow-duplicate"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**__import__("os").environ,
                 "KNOW_STATE_DIR": str(tmp_path / "state")},
        )
        time.sleep(2)
        p.terminate()
        p.wait(timeout=10)
        stderr = p.stderr.read() if p.stderr else ""
        assert "Refusing to start" not in stderr
    finally:
        lock.release()
