"""Orchestrator startup under DB lock contention."""

import sqlite3
import time
from datetime import datetime, timedelta

import pytest


def test_recover_orphaned_running_tasks_returns_count(tmp_path, monkeypatch):
    db_path = tmp_path / "tasks.db"
    monkeypatch.setattr("orchestrator.tasks.DB_PATH", db_path)
    monkeypatch.setattr("orchestrator.tasks.TASKS_DIR", tmp_path / "tasks")

    from orchestrator.tasks import TaskManager
    tm = TaskManager()

    old = (datetime.now() - timedelta(hours=5)).isoformat()
    fresh = datetime.now().isoformat()
    with tm._get_conn() as conn:
        for tid, started in [("t-old-1", old), ("t-old-2", old), ("t-fresh", fresh)]:
            conn.execute(
                "INSERT INTO tasks (id, action, domain, lang, status, started_at) "
                "VALUES (?, 'bootstrap', 'x', 'en', 'running', ?)",
                (tid, started),
            )
        conn.commit()

    count = tm.recover_orphaned_running_tasks()
    assert count == 2

    with tm._get_conn() as conn:
        rows = conn.execute("SELECT id, status FROM tasks ORDER BY id").fetchall()
    statuses = {r["id"]: r["status"] for r in rows}
    assert statuses["t-old-1"] == "interrupted"
    assert statuses["t-old-2"] == "interrupted"
    assert statuses["t-fresh"] == "running"


def test_task_manager_init_deferred_but_recovers(tmp_path, monkeypatch):
    """After external lock released, first DB op re-attempts schema init."""
    db_path = tmp_path / "tasks.db"
    monkeypatch.setattr("orchestrator.tasks.DB_PATH", db_path)
    monkeypatch.setattr("orchestrator.tasks.TASKS_DIR", tmp_path / "tasks")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()
    holder = sqlite3.connect(db_path, timeout=0)
    holder.execute("BEGIN IMMEDIATE")
    try:
        from orchestrator.tasks import TaskManager
        tm = TaskManager()
        assert tm._schema_ready is False
    finally:
        holder.rollback()
        holder.close()

    with tm._get_conn() as conn:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone()
        assert row is not None
