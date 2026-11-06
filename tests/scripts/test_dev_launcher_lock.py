"""Launcher-level lock: refuse second launch."""

import subprocess
import sys
import time
from pathlib import Path

import pytest


def test_second_launcher_refused(tmp_path, monkeypatch):
    """Acquire lock manually, then try to run launcher -- should refuse."""
    from quizkb.lock import acquire

    monkeypatch.setenv("KNOW_STATE_DIR", str(tmp_path / "state"))
    from quizkb.settings import reset_settings_cache
    reset_settings_cache()

    lock = acquire("know-dev-launcher",
                   metadata_dir=tmp_path / "state" / "locks",
                   command="test-holder")

    try:
        result = subprocess.run(
            [sys.executable, "tools/dev_launcher.py"],
            capture_output=True, text=True, timeout=15,
            env={**__import__("os").environ,
                 "KNOW_STATE_DIR": str(tmp_path / "state")},
        )
        assert result.returncode == 1, f"stdout={result.stdout} stderr={result.stderr}"
        assert "Refusing to start second launcher" in result.stderr
    finally:
        lock.release()