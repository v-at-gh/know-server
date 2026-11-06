"""I/O utilities for robust subprocess streaming and JSON file operations with mtime caching."""

import sys
import json
import uuid
import copy
from pathlib import Path
from typing import Any

_CACHE = {}
_STATS = {"hit": 0, "miss": 0}


class SafeStream:
    """Wrapper that swallows OSError on write/flush.

    Used for sys.stdout/sys.stderr when running as subprocess with a pipe
    that may be closed by the parent (worker) at any time. Once the pipe
    breaks, further writes are silently discarded. Behavior of the wrapped
    stream is unmodified until an OSError occurs.
    """

    def __init__(self, wrapped):
        self._wrapped = wrapped
        self._broken = False

    def write(self, s):
        if self._broken:
            return len(s) if isinstance(s, str) else 0
        try:
            return self._wrapped.write(s)
        except (OSError, ValueError):
            self._broken = True
            return len(s) if isinstance(s, str) else 0

    def flush(self):
        if self._broken:
            return
        try:
            self._wrapped.flush()
        except (OSError, ValueError):
            self._broken = True

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


def install_safe_std_streams():
    """Wrap sys.stdout and sys.stderr with SafeStream. Idempotent."""
    if not isinstance(sys.stdout, SafeStream):
        sys.stdout = SafeStream(sys.stdout)
    if not isinstance(sys.stderr, SafeStream):
        sys.stderr = SafeStream(sys.stderr)


def reset_cache() -> None:
    _CACHE.clear()
    _STATS["hit"] = 0
    _STATS["miss"] = 0


def cache_stats() -> dict:
    return dict(_STATS)


def load_json(path: Path | str, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return copy.deepcopy(default)
    try:
        mtime = p.stat().st_mtime
        key = str(p.resolve())
        cached = _CACHE.get(key)
        if cached is not None and cached["mtime"] == mtime:
            _STATS["hit"] += 1
            return copy.deepcopy(cached["data"])

        _STATS["miss"] += 1
        data = json.loads(p.read_text(encoding="utf-8"))
        _CACHE[key] = {"mtime": mtime, "data": data}
        return copy.deepcopy(data)
    except Exception:
        return copy.deepcopy(default)


def load_json_cached(path: Path | str, default: Any = None, cache: dict | None = None) -> Any:
    if cache is not None:
        p = Path(path)
        if not p.exists():
            return copy.deepcopy(default)
        key = str(p.resolve())
        if key in cache:
            return copy.deepcopy(cache[key])
        val = load_json(path, default=default)
        cache[key] = copy.deepcopy(val)
        return val
    return load_json(path, default=default)


def load_json_fresh(path: Path | str, default: Any = None) -> Any:
    return load_json(path, default=default)


def save_json(path: Path | str, data: Any, indent: int = 2) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    p.write_text(json.dumps(data, indent=indent, ensure_ascii=False), encoding="utf-8")
    key = str(p.resolve())
    if key in _CACHE:
        del _CACHE[key]


def save_json_atomic(path: Path | str, data: Any, indent: int = 2) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=indent, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    key = str(p.resolve())
    if key in _CACHE:
        del _CACHE[key]


def ensure_dir(path: Path | str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def new_uuid() -> str:
    return str(uuid.uuid4())


def strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def write_task_status(
    task_dir: Path,
    status: str,
    exit_code: int,
    error: str | None = None,
) -> None:
    """Best-effort write of status.json in a task directory.

    Called from subprocess finally blocks; must never raise. If write fails
    (disk full, permissions, missing parent), reaper falls back to
    output.log marker or interrupted classification.
    """
    import datetime as _dt
    try:
        data: dict = {
            "status": status,
            "exit_code": exit_code,
            "finished_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        if error:
            data["error"] = error
        (task_dir / "status.json").write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
