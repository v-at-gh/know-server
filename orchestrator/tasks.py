from __future__ import annotations

import logging
import json
import sqlite3
import uuid
import os
import time
from pathlib import Path
from datetime import datetime, timedelta
from contextlib import contextmanager
from quizkb.config import REPO_ROOT, TASK_PRIORITIES, AUTO_REINDEX_AFTER_GENERATE

TASKS_DIR = REPO_ROOT / "state" / "tasks"
DB_PATH = REPO_ROOT / "state" / "tasks.db"

log = logging.getLogger("orchestrator.tasks")


@contextmanager
def _transaction(conn):
    """Commit successful database work, roll back failures, and always close."""
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _tail(path: Path, n: int = 30) -> str:
    """Return the last N lines of a file without loading it into memory."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
            lines = data.decode("utf-8", errors="replace").splitlines()
            return "\n".join(lines[-n:])
    except Exception:
        return ""


class TaskManager:
    """Manages task queue and state for the distributed Orchestrator/Worker architecture.
    
    Acts as the persistent coordination layer backed by SQLite (`tasks.db`),
    handling enqueuing, atomic claiming, progress tracking, cancellation requests,
    and startup recovery across backend restarts.
    """
    def __init__(self):
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        self._schema_ready = False
        try:
            self._init_db()
            self._schema_ready = True
        except sqlite3.OperationalError as e:
            log.warning("tasks_db_init_deferred_at_construction",
                        extra={"error": str(e)})

    def _get_conn(self):
        if not getattr(self, "_schema_ready", True):
            try:
                self._init_db()
                self._schema_ready = True
            except sqlite3.OperationalError:
                pass
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes tasks schema, migrates legacy `meta.json` records, and 
        applies timeout safety nets for orphaned tasks in the distributed architecture.
        """
        with _transaction(sqlite3.connect(DB_PATH, timeout=30)) as conn:
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        action TEXT NOT NULL,
                        domain TEXT NOT NULL,
                        lang TEXT NOT NULL,
                        status TEXT NOT NULL,
                        pid INTEGER,
                        started_at TEXT,
                        finished_at TEXT,
                        error_message TEXT,
                        kill_requested INTEGER DEFAULT 0
                    )
                """)
                try:
                    conn.execute("ALTER TABLE tasks ADD COLUMN username TEXT DEFAULT 'system'")
                except sqlite3.OperationalError: pass
                try:
                    conn.execute("ALTER TABLE tasks ADD COLUMN kill_requested INTEGER DEFAULT 0")
                except sqlite3.OperationalError: pass
                try:
                    conn.execute("ALTER TABLE tasks ADD COLUMN target_uuid TEXT")
                except sqlite3.OperationalError: pass
                try:
                    conn.execute("ALTER TABLE tasks ADD COLUMN target_article_key TEXT")
                except sqlite3.OperationalError: pass
                try:
                    conn.execute("ALTER TABLE tasks ADD COLUMN generation_params TEXT")
                except sqlite3.OperationalError: pass
                try:
                    conn.execute("ALTER TABLE tasks ADD COLUMN target_count INTEGER")
                except sqlite3.OperationalError: pass
                try:
                    conn.execute("ALTER TABLE tasks ADD COLUMN trace_parent TEXT")
                except sqlite3.OperationalError: pass
                try:
                    conn.execute("ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'generation'")
                except sqlite3.OperationalError: pass
                try:
                    conn.execute("ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
                except sqlite3.OperationalError: pass

                # 1. Migrate old meta.json tasks
                if TASKS_DIR.exists():
                    for task_dir in TASKS_DIR.iterdir():
                        if not task_dir.is_dir(): continue
                        meta_path = task_dir / "meta.json"
                        if not meta_path.exists(): continue
                        try:
                            meta = json.loads(meta_path.read_text(encoding="utf-8"))
                            task_id = meta.get("id")
                            if not task_id: continue
                            row = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
                            if row: continue
                            status = meta.get("status", "done")
                            if status == "running":
                                status = "interrupted"
                            conn.execute(
                                "INSERT INTO tasks (id, action, domain, lang, status, started_at, error_message) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (task_id, meta.get("type", "unknown"), meta.get("domain", ""), meta.get("lang", ""), status, meta.get("started_at"), meta.get("error"))
                            )
                        except Exception as e:
                            print(f"Failed to migrate task {task_dir.name}: {e}")
            except sqlite3.OperationalError as e:
                log.warning("tasks_db_init_deferred", extra={"error": str(e)})
                raise

    def recover_orphaned_running_tasks(self) -> int:
        """Mark tasks that have been 'running' for >4h as interrupted.

        Safe to call repeatedly. Returns count of affected rows.
        Raises sqlite3.OperationalError if DB write is locked (caller decides retry).
        """
        cutoff = (datetime.now() - timedelta(hours=4)).isoformat()
        with _transaction(self._get_conn()) as conn:
            cursor = conn.execute("""
                UPDATE tasks 
                SET status = 'interrupted', error_message = 'Task timeout (>4h)', finished_at = ?
                WHERE status = 'running' AND started_at < ?
            """, (datetime.now().isoformat(), cutoff))
            return cursor.rowcount

    def reconcile_orphaned_tasks(self) -> dict:
        """Reconcile running tasks whose subprocess is dead.

        Called periodically by orchestrator background loop. Uses psutil to
        check each running task's pid; on dead/missing, reads status.json
        (or falls back to output.log marker) to determine final status.

        Returns {"scanned": N, "reconciled": M, "skipped": K}.
        """
        result = {"scanned": 0, "reconciled": 0, "skipped": 0}

        with _transaction(self._get_conn()) as conn:
            rows = conn.execute(
                "SELECT id, pid, started_at FROM tasks WHERE status='running'"
            ).fetchall()

        result["scanned"] = len(rows)

        for row in rows:
            task_id = row["id"]
            pid = row["pid"]
            started_at = row["started_at"]

            decision = self._classify_running_task(task_id, pid, started_at)

            if decision["action"] == "skip":
                result["skipped"] += 1
                log.debug(
                    "reconcile_skip",
                    extra={"task_id": task_id, "reason": decision["reason"]},
                )
                continue

            status = decision["status"]
            err_msg = decision.get("error_message")
            try:
                self.update_task_status(task_id, status, error_message=err_msg)
                result["reconciled"] += 1
                log.info(
                    "reconcile_transition",
                    extra={"task_id": task_id, "status": status, "reason": decision["reason"]},
                )
            except Exception:
                log.exception("reconcile_update_failed", extra={"task_id": task_id})

        return result

    def _classify_running_task(
        self, task_id: str, pid: int | None, started_at: str,
    ) -> dict:
        """Decide what to do with a running task.

        Returns {"action": "skip"|"transition", "reason": str,
                 "status": str?, "error_message": str?}
        """
        import psutil
        from datetime import datetime

        if pid is not None:
            try:
                proc = psutil.Process(pid)
                if proc.status() == psutil.STATUS_ZOMBIE:
                    return self._reconcile_from_marker(task_id, reason="pid_zombie")
                cmdline = " ".join(proc.cmdline() or [])
                if ("generate_content.py" in cmdline or "reindex_vec.py" in cmdline) and task_id in cmdline:
                    return {"action": "skip", "reason": "process_alive"}
                return self._reconcile_from_marker(task_id, reason="pid_reused")
            except psutil.NoSuchProcess:
                return self._reconcile_from_marker(task_id, reason="pid_dead")
            except psutil.AccessDenied:
                return {"action": "skip", "reason": "access_denied"}
            except Exception:
                return {"action": "skip", "reason": "inspect_error"}

        try:
            started = datetime.fromisoformat(started_at)
            age_sec = (datetime.now() - started).total_seconds()
        except Exception:
            age_sec = 999999

        if age_sec < 60:
            return {"action": "skip", "reason": "pre_spawn_window"}

        if self._scan_for_task_id(task_id):
            return {"action": "skip", "reason": "found_via_scan"}

        return self._reconcile_from_marker(task_id, reason="worker_died_before_spawn")

    def _scan_for_task_id(self, task_id: str) -> bool:
        """Scan all pids for a cmdline match. Returns True if found alive."""
        import psutil
        try:
            for proc in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cmdline = " ".join(proc.info.get("cmdline") or [])
                    if ("generate_content.py" in cmdline or "reindex_vec.py" in cmdline) and task_id in cmdline:
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass
        return False

    def _reconcile_from_marker(self, task_id: str, reason: str) -> dict:
        """Read status.json (or output.log marker fallback), return decision."""
        status_path = TASKS_DIR / task_id / "status.json"
        if status_path.exists():
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
                file_status = data.get("status")
                if file_status == "done":
                    return {"action": "transition", "status": "done", "reason": reason}
                if file_status == "error":
                    return {
                        "action": "transition", "status": "error",
                        "error_message": data.get("error") or f"Orphan reconciled: {reason}",
                        "reason": reason,
                    }
                return {
                    "action": "transition", "status": "interrupted",
                    "error_message": f"Orphan reconciled: {reason} (marker status={file_status!r})",
                    "reason": reason,
                }
            except Exception:
                return {
                    "action": "transition", "status": "interrupted",
                    "error_message": f"Orphan reconciled: {reason} (status.json unreadable)",
                    "reason": reason,
                }

        log_path = TASKS_DIR / task_id / "output.log"
        if log_path.exists():
            try:
                content = log_path.read_text(encoding="utf-8", errors="replace")
                tail = content[-4096:] if len(content) > 4096 else content
                if "=== Reindex Completed Successfully ===" in tail:
                    return {"action": "transition", "status": "done", "reason": f"{reason}_log_marker"}
            except Exception:
                pass

        return {
            "action": "transition", "status": "interrupted",
            "error_message": f"Orphan reconciled: {reason} (no marker)",
            "reason": reason,
        }

    def enqueue(
        self,
        action: str,
        domain: str = "",
        lang: str = "",
        username: str = "system",
        kind: str = "generation",
        priority: int | None = None,
        dedup_scope: str = "queued+running",  # 'queued+running' | 'queued'
        force: bool = False,
        target_uuid: str | None = None,
        target_article_key: str | None = None,
        target_count: int | None = None,
        trace_parent: str | None = None,
        generation_params: dict | str | None = None,
    ) -> str:
        if priority is None:
            priority = TASK_PRIORITIES.get(action, 0)

        with _transaction(self._get_conn()) as conn:
            if not force:
                status_clause = "status IN ('queued', 'running')" if dedup_scope == "queued+running" else "status = 'queued'"
                row = conn.execute(
                    f"SELECT id FROM tasks WHERE kind=? AND action=? AND domain=? AND lang=? "
                    f"AND COALESCE(target_uuid, '') = COALESCE(?, '') "
                    f"AND COALESCE(target_article_key, '') = COALESCE(?, '') "
                    f"AND {status_clause}",
                    (kind, action, domain, lang, target_uuid or "", target_article_key or ""),
                ).fetchone()
                if row:
                    log.info("Dedup hit for %s %s -> %s (uuid=%s, article_key=%s), returning existing id=%s",
                             action, domain, lang, target_uuid, target_article_key, row["id"])
                    return row["id"]

            gp_str = json.dumps(generation_params) if isinstance(generation_params, dict) else generation_params
            short_uuid = uuid.uuid4().hex[:8]
            now = datetime.now()
            task_id = f"{now.strftime('%Y-%m-%d_%H-%M-%S')}_{short_uuid}"
            conn.execute(
                "INSERT INTO tasks (id, action, domain, lang, status, started_at, username, "
                "kill_requested, target_uuid, target_article_key, target_count, trace_parent, "
                "generation_params, kind, priority) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, action, domain, lang, now.isoformat(), username,
                 target_uuid, target_article_key, target_count, trace_parent,
                 gp_str, kind, priority),
            )
            conn.commit()

        task_dir = TASKS_DIR / task_id
        task_dir.mkdir(exist_ok=True)
        (task_dir / "output.log").write_text("Waiting in queue...\n", encoding="utf-8")

        log.info("Queued task %s: %s %s -> %s", task_id, action, domain, lang)
        return task_id

    def list_tasks(self, sort: str = "started_at", direction: str = "DESC", page: int = 1, limit: int = 50, status: str | None = None, domain: str | None = None, lang: str | None = None) -> tuple[list[dict], int]:
        allowed_sorts = {"id", "username", "status", "action", "domain", "lang", "started_at"}
        if sort not in allowed_sorts:
            sort = "started_at"
        
        direction = "ASC" if direction.upper() == "ASC" else "DESC"
        offset = (page - 1) * limit
        
        where_clauses = []
        params = []
        if status:
            where_clauses.append("status = ?")
            params.append(status)
        if domain:
            where_clauses.append("domain = ?")
            params.append(domain)
        if lang:
            where_clauses.append("lang = ?")
            params.append(lang)
            
        where_str = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        
        with _transaction(self._get_conn()) as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM tasks {where_str}", params).fetchone()[0]
            query = f"""
                SELECT * FROM tasks 
                {where_str}
                ORDER BY {sort} {direction}
                LIMIT ? OFFSET ?
            """
            rows = conn.execute(query, params + [limit, offset]).fetchall()
            return [dict(r) for r in rows], total

    def get_task(self, task_id: str) -> dict | None:
        with _transaction(self._get_conn()) as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return None
            task = dict(row)

        progress_path = TASKS_DIR / task_id / "progress.json"
        if progress_path.exists():
            try:
                task["progress"] = json.loads(progress_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return task

    def get_active_count(self) -> int:
        with _transaction(self._get_conn()) as conn:
            row = conn.execute("SELECT COUNT(*) as c FROM tasks WHERE status IN ('queued', 'running')").fetchone()
            return row["c"]

    def claim_next_task(self) -> dict | None:
        with _transaction(self._get_conn()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE status = 'queued' ORDER BY priority DESC, started_at ASC LIMIT 1"
                ).fetchone()
                if not row:
                    conn.rollback()
                    return None
                task = dict(row)
                task["status"] = "running"
                conn.execute(
                    "UPDATE tasks SET status = 'running', started_at = ? WHERE id = ?",
                    (datetime.now().isoformat(), task["id"])
                )
                conn.commit()
                return task
            except Exception:
                conn.rollback()
                raise

    def update_task_status(self, task_id: str, status: str, pid: int = None, error_message: str = None):
        task_kind = None
        updated = False
        with _transaction(self._get_conn()) as conn:
            row = conn.execute("SELECT kind FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row:
                task_kind = row["kind"]

            finished_at = datetime.now().isoformat() if status in ('done', 'error', 'killed', 'interrupted') else None
            if status in ('done', 'error', 'killed', 'interrupted'):
                cur = conn.execute("""
                    UPDATE tasks
                    SET status = ?, pid = COALESCE(?, pid), error_message = ?, finished_at = ?
                    WHERE id = ? AND status IN ('running', 'queued')
                """, (status, pid, error_message, finished_at, task_id))
                updated = cur.rowcount > 0
            else:
                cur = conn.execute("""
                    UPDATE tasks
                    SET status = ?, pid = COALESCE(?, pid), error_message = ?, finished_at = ?
                    WHERE id = ?
                """, (status, pid, error_message, finished_at, task_id))
                updated = cur.rowcount > 0

        if updated and status == 'done' and task_kind == 'generation' and AUTO_REINDEX_AFTER_GENERATE:
            try:
                self.enqueue(
                    action="reindex_missing",
                    kind="reindex",
                    username="system",
                    dedup_scope="queued",
                    force=False,
                )
            except Exception:
                log.exception("auto_reindex_enqueue_failed", extra={"after_task_id": task_id})

    def kill(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if not task or task["status"] not in ("queued", "running"):
            return False

        if task["status"] == "queued":
            self.update_task_status(task_id, "killed", error_message="Manually killed by user")
            return True

        with _transaction(self._get_conn()) as conn:
            conn.execute("UPDATE tasks SET kill_requested = 1 WHERE id = ?", (task_id,))
            conn.commit()
        return True

    def restart(self, task_id: str, username: str = "system", trace_parent: str | None = None, wait_for_exit: bool = True) -> str | None:
        old_task = self.get_task(task_id)
        if not old_task:
            return None

        if old_task["status"] in ("queued", "running"):
            self.kill(task_id)

        if wait_for_exit and old_task.get("pid"):
            pid = old_task["pid"]
            start_time = time.time()
            while time.time() - start_time < 5.0:
                alive = False
                try:
                    import psutil
                    alive = psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
                except Exception:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except OSError:
                        alive = False
                if not alive:
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError(f"Task {task_id} process (pid {pid}) failed to exit within 5 seconds.")

        new_id = self.enqueue(
            action=old_task["action"],
            domain=old_task["domain"],
            lang=old_task["lang"],
            username=username,
            kind=old_task.get("kind", "generation"),
            priority=old_task.get("priority"),
            force=True,
            target_uuid=old_task.get("target_uuid"),
            target_article_key=old_task.get("target_article_key"),
            target_count=old_task.get("target_count"),
            trace_parent=trace_parent,
        )
        return new_id
