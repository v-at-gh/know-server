"""Kernel-managed process lock tests. Isolated tmp_path only."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from quizkb.lock import acquire, LockAlreadyHeld


@pytest.fixture
def lock_dir(tmp_path):
    d = tmp_path / "locks"
    d.mkdir()
    return d


def test_acquire_and_release(lock_dir):
    lock = acquire("test-basic", metadata_dir=lock_dir)
    assert (lock_dir / "test-basic.owner.json").exists()
    lock.release()
    assert not (lock_dir / "test-basic.owner.json").exists()


def test_second_acquire_raises(lock_dir):
    lock1 = acquire("test-dup", metadata_dir=lock_dir)
    try:
        with pytest.raises(LockAlreadyHeld) as exc_info:
            acquire("test-dup", metadata_dir=lock_dir)
        assert exc_info.value.name == "test-dup"
        assert exc_info.value.owner_metadata.get("pid") == os.getpid()
    finally:
        lock1.release()


def test_release_allows_reacquire(lock_dir):
    lock1 = acquire("test-reacq", metadata_dir=lock_dir)
    lock1.release()
    lock2 = acquire("test-reacq", metadata_dir=lock_dir)
    lock2.release()


def test_metadata_contains_pid_and_command(lock_dir):
    import json
    lock = acquire("test-meta", metadata_dir=lock_dir, command="test-cmd --foo")
    try:
        meta = json.loads((lock_dir / "test-meta.owner.json").read_text())
        assert meta["pid"] == os.getpid()
        assert meta["command"] == "test-cmd --foo"
        assert "started_at" in meta
    finally:
        lock.release()


def test_concurrent_processes_only_one_wins(lock_dir):
    """Spawn two subprocesses racing for same lock; exactly one should hold.

    Barrier: p1 spawns, we block on its first stdout line ("ACQUIRED").
    Only after seeing ACQUIRED do we spawn p2. This eliminates the sleep-
    based race and works reliably on slow CI.
    """
    script = f"""
import sys, time
sys.path.insert(0, {repr(str(Path.cwd()))})
from quizkb.lock import acquire, LockAlreadyHeld
try:
    lock = acquire("test-race", metadata_dir={repr(str(lock_dir))})
    print("ACQUIRED", flush=True)
    time.sleep(2)
    lock.release()
    print("RELEASED", flush=True)
except LockAlreadyHeld:
    print("BLOCKED", flush=True)
"""
    p1 = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, text=True,
    )
    # Barrier: block until p1 explicitly confirms ACQUIRED
    p1_line1 = p1.stdout.readline()
    assert "ACQUIRED" in p1_line1, f"p1 did not acquire: {p1_line1!r}"

    p2 = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, text=True,
    )
    p2_line1 = p2.stdout.readline()

    # Drain remaining output; assertions on line1 values, not on communicate() tail
    p1_out_rest, _ = p1.communicate(timeout=10)
    p2_out_rest, _ = p2.communicate(timeout=10)

    assert "BLOCKED" in p2_line1, f"p2 should be blocked, got: {p2_line1!r}"
    assert "RELEASED" in p1_out_rest, f"p1 should release, got: {p1_out_rest!r}"