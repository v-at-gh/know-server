"""Append-only JSONL logger for a single pipeline run.

Layout:
    <root>/<run_id>/
        meta.json        # written once at start
        llm.jsonl        # one object per ask() call
        pipeline.jsonl   # progress marks, file writes, cell boundaries
        errors.jsonl     # parse rejects, structured failures
    <root>/index.jsonl   # one line per closed run
    <root>/archive/      # tar.gz of pruned runs

Every write flushes immediately. If the kernel dies, at most the last
partial line is lost; earlier lines are intact.

Cell tagging:
    with logger.cell("H"):
        ...       # all events inside get "cell": "H"

Nested contexts are allowed; the innermost active cell wins. The
cell() context manager also emits cell_start / cell_end markers into
pipeline.jsonl, so cell timings can be reconstructed from a single file.

Usage:
    from quizkb.logging import Logger
    from quizkb.config import LOGS_ROOT, LOGGING_KEEP_LAST_RUNS

    logger = Logger(
        root=LOGS_ROOT,
        keep_last_runs=LOGGING_KEEP_LAST_RUNS,
        config_snapshot={"TARGET_LANGS": [...], "DOMAINS": [...]},
        pipeline_rev_cwd=Path("."),
    )
    # ... pipeline work, ask() calls logger.llm(...) internally ...
    logger.close()   # also registered with atexit
"""
from __future__ import annotations

import atexit
import hashlib
import json
import platform
import shutil
import socket
import subprocess
import tarfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DEFAULT_KEEP_LAST_RUNS = 20


# ---------- small helpers ----------

_CANONICAL_REASONS = frozenset({
    "parse_reject",
    "parse_reject_empty",
    "no_response",
    "duplicate",
    "too_short",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _machine_tag() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    if system == "linux":
        # If running under WSL, system is Linux; that's fine, distinct from macos.
        return "linux"
    return system or "unknown"


def _git_rev(cwd: Path | None) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _sha256_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ---------- Logger ----------

class Logger:

    def __init__(
        self,
        root: Path,
        *,
        run_id: str | None = None,
        config_snapshot: dict | None = None,
        pipeline_rev_cwd: Path | None = None,
        keep_last_runs: int = DEFAULT_KEEP_LAST_RUNS,
    ) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._keep_last = int(keep_last_runs)
        self._lock = threading.RLock()
        self._cell_stack: list[str] = []
        self._closed = False

        if run_id is None:
            base = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
            run_id = f"{base}-{_machine_tag()}"

        # Avoid collisions if two runs start in the same second.
        self.run_id = run_id
        self.run_dir = self._root / self.run_id
        suffix = 1
        while self.run_dir.exists():
            self.run_id = f"{run_id}-{suffix}"
            self.run_dir = self._root / self.run_id
            suffix += 1
        self.run_dir.mkdir(parents=True, exist_ok=False)

        self._fh_llm = (self.run_dir / "llm.jsonl").open("a", encoding="utf-8")
        self._fh_pipeline = (self.run_dir / "pipeline.jsonl").open("a", encoding="utf-8")
        self._fh_errors = (self.run_dir / "errors.jsonl").open("a", encoding="utf-8")

        started_at = _now_iso()
        self._meta = {
            "run_id": self.run_id,
            "started_at": started_at,
            "machine": _machine_tag(),
            "pipeline_git_rev": _git_rev(pipeline_rev_cwd),
            "config_snapshot": config_snapshot or {},
        }
        (self.run_dir / "meta.json").write_text(
            json.dumps(self._meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        self.event(kind="run_start", run_id=self.run_id, started_at=started_at)

        # Best-effort close on interpreter shutdown.
        atexit.register(self.close)

    # ---------- cell context ----------

    @contextmanager
    def cell(self, name: str) -> Iterator[None]:
        self._cell_stack.append(name)
        self.event(kind="cell_start", cell_name=name)
        try:
            yield
        finally:
            self.event(kind="cell_end", cell_name=name)
            self._cell_stack.pop()

    # ---------- public write methods ----------

    def llm(
        self,
        *,
        label: str,
        prompt: str,
        response: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        model: str | None = None,
        think: bool | None = None,
        error: str | None = None,
    ) -> None:
        self._write(self._fh_llm, {
            "kind": "llm",
            "ts": _now_iso(),
            "cell": self._current_cell(),
            "label": label,
            "model": model,
            "think": think,
            "prompt": prompt,
            "response": response,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "latency_ms": latency_ms,
            "error": error,
        })

    def progress(self, *, event: str, key: str) -> None:
        self._write(self._fh_pipeline, {
            "kind": "progress",
            "ts": _now_iso(),
            "cell": self._current_cell(),
            "event": event,
            "key": key,
        })

    def write(self, *, path: Path | str, content: str | bytes) -> None:
        data = content.encode("utf-8") if isinstance(content, str) else content
        self._write(self._fh_pipeline, {
            "kind": "write",
            "ts": _now_iso(),
            "cell": self._current_cell(),
            "path": str(path),
            "bytes": len(data),
            "sha256": _sha256_of(data),
        })

    def parse_reject(
        self,
        label: str,
        reason: str,
        prompt: str = "",
        raw_response: str = "",
        detail: str = "",
    ) -> None:
        if not detail and ": " in reason:
            prefix, _, rest = reason.partition(": ")
            if prefix in _CANONICAL_REASONS:
                reason, detail = prefix, rest
        self._write(self._fh_errors, {
            "kind": "parse_reject",
            "ts": _now_iso(),
            "cell": self._current_cell(),
            "label": label,
            "reason": reason,
            "detail": detail,
            "prompt": prompt,
            "raw_response": raw_response,
        })

    def event(self, *, kind: str, **fields: Any) -> None:
        """Escape hatch for one-off events (release gate outcomes, etc.)."""
        payload = {
            "kind": kind,
            "ts": _now_iso(),
            "cell": self._current_cell(),
        }
        payload.update(fields)
        self._write(self._fh_pipeline, payload)

    # ---------- lifecycle ----------

    def close(self) -> None:
        """Idempotent. Acquires self._lock (RLock) and emits run_end via
        self.event(), which itself acquires the same lock in _write().
        RLock is required here — a plain Lock deadlocks."""
        if self._closed:
            return
        with self._lock:
            self._closed = True
            self.event(kind="run_end", run_id=self.run_id)
            for fh in (self._fh_llm, self._fh_pipeline, self._fh_errors):
                try:
                    fh.flush()
                    fh.close()
                except Exception:
                    pass
            self._append_run_index()
        self._prune_old_runs()

    # ---------- internals ----------

    def _current_cell(self) -> str | None:
        return self._cell_stack[-1] if self._cell_stack else None

    def _write(self, fh, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        with self._lock:
            fh.write(line)
            fh.flush()

    def _append_run_index(self) -> None:
        entry = {
            "run_id": self.run_id,
            "started_at": self._meta.get("started_at"),
            "ended_at": _now_iso(),
            "lines": {
                "llm": self._count_lines(self.run_dir / "llm.jsonl"),
                "pipeline": self._count_lines(self.run_dir / "pipeline.jsonl"),
                "errors": self._count_lines(self.run_dir / "errors.jsonl"),
            },
        }
        with (self._root / "index.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def _count_lines(path: Path) -> int:
        try:
            with path.open("rb") as fh:
                return sum(1 for _ in fh)
        except FileNotFoundError:
            return 0

    def _prune_old_runs(self) -> None:
        if self._keep_last <= 0:
            return
        candidates = sorted(
            (p for p in self._root.iterdir()
             if p.is_dir() and p.name not in ("archive", self.run_id)),
            reverse=True,
        )
        # Keep (keep_last - 1) most recent non-current runs; current counts as one.
        to_archive = candidates[self._keep_last - 1:]
        if not to_archive:
            return
        archive_dir = self._root / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        for run_dir in to_archive:
            target = archive_dir / f"{run_dir.name}.tar.gz"
            if target.exists():
                continue
            try:
                with tarfile.open(target, "w:gz") as tar:
                    tar.add(run_dir, arcname=run_dir.name)
                shutil.rmtree(run_dir, ignore_errors=True)
            except OSError:
                # Best-effort; leave the run in place if archiving failed.
                continue