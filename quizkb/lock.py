"""Kernel-managed process singleton lock.

Cross-platform atomic lock primitive. Rejects already-holding process at
acquire() time. **The OS releases the lock when the holding process dies
by any means** (clean exit, kill, crash, terminal close, TerminateProcess
on Windows). No stale-lock recovery, no atexit, no PID files.

Design notes:
- Windows: named mutex (CreateMutexW) with unique namespace prefix.
  Kernel auto-releases on handle close, which happens on process termination
  including TerminateProcess (Microsoft-documented Windows behavior).
- POSIX: fcntl.flock on a file. The fd is kept open for process lifetime;
  kernel releases flock when fd closes on process death.
- Lock file path on POSIX is created adjacent to state dir; on Windows the
  mutex has no filesystem footprint. Lock metadata (PID, start time, command
  line) written to a metadata file for operator debugging ONLY -- not used
  for lock ownership decisions.
- Concurrent acquisition is atomic. Second process gets LockAlreadyHeld and
  can surface owner metadata.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

log = logging.getLogger(__name__)


class LockAlreadyHeld(RuntimeError):
    """Raised when acquire() fails because another process holds the lock."""

    def __init__(self, name: str, owner_metadata: dict | None = None):
        self.name = name
        self.owner_metadata = owner_metadata or {}
        pid = self.owner_metadata.get("pid", "?")
        cmd = self.owner_metadata.get("command", "?")
        started = self.owner_metadata.get("started_at", "?")
        super().__init__(
            f"Lock '{name}' is held by pid={pid} (started={started}, cmd={cmd})"
        )


@dataclass
class _Handle:
    """Opaque handle. Interpretation depends on platform."""
    platform: str
    name: str
    win_handle: Any = None  # Windows: int HANDLE
    posix_fd: IO | None = None
    metadata_path: Path | None = None


class ProcessLock:
    """Acquired lock. Call release() or let GC/process-exit handle it.

    Explicit release() is recommended for clean shutdown, but kernel
    guarantees release on any process death -- callers may skip release
    if uncertain whether exceptions will propagate.
    """

    def __init__(self, handle: _Handle):
        self._handle = handle
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._handle.platform == "win":
            import ctypes
            if self._handle.win_handle:
                ctypes.windll.kernel32.ReleaseMutex(self._handle.win_handle)
                ctypes.windll.kernel32.CloseHandle(self._handle.win_handle)
        else:
            if self._handle.posix_fd:
                try:
                    self._handle.posix_fd.close()
                except OSError:
                    pass
        if self._handle.metadata_path and self._handle.metadata_path.exists():
            try:
                self._handle.metadata_path.unlink()
            except OSError:
                pass

    def __enter__(self) -> "ProcessLock":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def acquire(
    name: str,
    *,
    metadata_dir: Path | None = None,
    command: str | None = None,
) -> ProcessLock:
    """Acquire OS-level singleton. Raises LockAlreadyHeld if occupied.

    Args:
        name: Unique lock name (e.g. "know-dev-launcher", "know-worker").
        metadata_dir: Directory for optional metadata file. Defaults to
            get_settings().state_dir / "locks". Metadata is diagnostic only.
        command: Optional command line for metadata (default: sys.argv joined).
    """
    if metadata_dir is None:
        from quizkb.settings import get_settings
        metadata_dir = get_settings().state_dir / "locks"
    else:
        metadata_dir = Path(metadata_dir)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = metadata_dir / f"{name}.owner.json"

    if sys.platform == "win32":
        handle = _acquire_windows(name, metadata_path, command)
    else:
        handle = _acquire_posix(name, metadata_dir, metadata_path, command)

    return ProcessLock(handle)


def _write_metadata(path: Path, command: str | None) -> None:
    """Best-effort metadata for operator debugging. Never raises."""
    try:
        path.write_text(
            json.dumps({
                "pid": os.getpid(),
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "command": command or " ".join(sys.argv),
                "platform": sys.platform,
            }, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _read_metadata(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _acquire_windows(name: str, metadata_path: Path, command: str | None) -> _Handle:
    """Windows: named mutex. Atomic via CreateMutexW + WaitForSingleObject.

    Namespace: 'Global\\' prefix would need admin. Use 'Local\\' which is
    per-session -- sufficient for same-machine single-user workflows (current
    deployment). Multi-user Windows would need Global\\ + admin; deferred.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD

    ERROR_ALREADY_EXISTS = 183
    WAIT_OBJECT_0 = 0
    WAIT_ABANDONED = 0x80
    WAIT_TIMEOUT = 0x102

    mutex_name = f"Local\\know-{name}"

    handle = kernel32.CreateMutexW(None, True, mutex_name)
    if not handle:
        err = ctypes.get_last_error()
        raise RuntimeError(f"CreateMutexW failed: GetLastError={err}")

    last_err = ctypes.get_last_error()

    if last_err == ERROR_ALREADY_EXISTS:
        wait_result = kernel32.WaitForSingleObject(handle, 0)
        if wait_result == WAIT_TIMEOUT:
            owner = _read_metadata(metadata_path)
            kernel32.CloseHandle(handle)
            raise LockAlreadyHeld(name, owner)
        elif wait_result in (WAIT_OBJECT_0, WAIT_ABANDONED):
            pass
        else:
            kernel32.CloseHandle(handle)
            raise RuntimeError(f"WaitForSingleObject returned {wait_result:#x}")

    _write_metadata(metadata_path, command)
    return _Handle(platform="win", name=name, win_handle=handle,
                   metadata_path=metadata_path)


def _acquire_posix(
    name: str,
    metadata_dir: Path,
    metadata_path: Path,
    command: str | None,
) -> _Handle:
    """POSIX: fcntl.flock, non-blocking. Kernel releases on process death."""
    import fcntl

    lock_file = metadata_dir / f"{name}.lock"
    fd = open(lock_file, "a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        fd.close()
        owner = _read_metadata(metadata_path)
        raise LockAlreadyHeld(name, owner) from e

    _write_metadata(metadata_path, command)
    return _Handle(platform="posix", name=name, posix_fd=fd,
                   metadata_path=metadata_path)
