import os
import sqlite3
import json
import sys
import io
from pathlib import Path
import pytest
from scripts.knowctl import cmd_backup, cmd_tasks_interrupt, cmd_status

def test_backup_dry_run_creates_no_files(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    backups_dir = state_dir / "backups"
    state_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)

    content_db = state_dir / "content.db"
    with sqlite3.connect(content_db) as conn:
        conn.execute("CREATE TABLE test (id INT)")
        conn.commit()

    monkeypatch.setattr("scripts.knowctl.REPO_ROOT", tmp_path)

    class Args:
        content_only = True
        output_only = False
        state_only = False
        dry_run = True
        verbose = 0
        json = False

    captured = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', captured)
    
    with pytest.raises(SystemExit) as exc:
        cmd_backup(Args())
    assert exc.value.code == 0
    assert len(list(backups_dir.glob("*"))) == 0
    assert "Dry run: backup" in captured.getvalue()

def test_backup_dry_run_reports_sizes(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    backups_dir = state_dir / "backups"
    state_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)

    content_db = state_dir / "content.db"
    with sqlite3.connect(content_db) as conn:
        conn.execute("CREATE TABLE test (id INT)")
        conn.commit()

    monkeypatch.setattr("scripts.knowctl.REPO_ROOT", tmp_path)

    class Args:
        content_only = True
        output_only = False
        state_only = False
        dry_run = True
        verbose = 0
        json = False

    captured = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', captured)
    with pytest.raises(SystemExit) as exc:
        cmd_backup(Args())
    output = captured.getvalue()
    assert "content.db:" in output
    assert "bytes" in output

def test_backup_dry_run_fails_on_missing_source(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    backups_dir = state_dir / "backups"
    state_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("scripts.knowctl.REPO_ROOT", tmp_path)

    class Args:
        content_only = True
        output_only = False
        state_only = False
        dry_run = True
        verbose = 0
        json = False

    err_captured = io.StringIO()
    monkeypatch.setattr(sys, 'stderr', err_captured)

    with pytest.raises(SystemExit) as exc:
        cmd_backup(Args())
    assert exc.value.code == 1
    assert "not found" in err_captured.getvalue()

def test_backup_dry_run_does_not_create_parent(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.knowctl.REPO_ROOT", tmp_path)

    class Args:
        content_only = True
        output_only = False
        state_only = False
        dry_run = True
        verbose = 0
        json = False

    err_captured = io.StringIO()
    monkeypatch.setattr(sys, 'stderr', err_captured)

    with pytest.raises(SystemExit) as exc:
        cmd_backup(Args())
    assert exc.value.code == 1
    assert not (tmp_path / "state").exists()

def test_interrupt_dry_run_no_db_change(tmp_path, monkeypatch):
    tasks_db = tmp_path / "state" / "tasks.db"
    tasks_db.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(tasks_db) as conn:
        conn.execute("""
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                action TEXT,
                domain TEXT,
                lang TEXT,
                status TEXT,
                pid INT,
                username TEXT,
                started_at TEXT
            )
        """)
        conn.execute("INSERT INTO tasks VALUES ('task_123', 'bootstrap', 'dom', 'en', 'running', 123, 'system', '2026-10-26T00:00:01')")
        conn.commit()

    monkeypatch.setattr("scripts.knowctl.REPO_ROOT", tmp_path)

    class Args:
        task_id = "task_123"
        dry_run = True
        yes = True
        verbose = 0
        json = False

    captured = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', captured)
    with pytest.raises(SystemExit) as exc:
        cmd_tasks_interrupt(Args())
    assert exc.value.code == 0

    # Verify status unchanged in DB
    with sqlite3.connect(tasks_db) as conn:
        status = conn.execute("SELECT status FROM tasks WHERE id = 'task_123'").fetchone()[0]
        assert status == "running"

def test_verbose_does_not_pollute_json(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    backups_dir = state_dir / "backups"
    state_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)

    content_db = state_dir / "content.db"
    with sqlite3.connect(content_db) as conn:
        conn.execute("CREATE TABLE test (id INT)")
        conn.commit()

    monkeypatch.setattr("scripts.knowctl.REPO_ROOT", tmp_path)

    class Args:
        content_only = True
        output_only = False
        state_only = False
        dry_run = False
        verbose = 1
        json = True

    stdout_cap = io.StringIO()
    stderr_cap = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', stdout_cap)
    monkeypatch.setattr(sys, 'stderr', stderr_cap)

    cmd_backup(Args())
    
    # stdout should be valid JSON
    data = json.loads(stdout_cap.getvalue())
    assert "content_db" in data
    
    # stderr should contain [debug]
    assert "[debug]" in stderr_cap.getvalue()

def test_verbose_default_quiet(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    backups_dir = state_dir / "backups"
    state_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)

    content_db = state_dir / "content.db"
    with sqlite3.connect(content_db) as conn:
        conn.execute("CREATE TABLE test (id INT)")
        conn.commit()

    monkeypatch.setattr("scripts.knowctl.REPO_ROOT", tmp_path)

    class Args:
        content_only = True
        output_only = False
        state_only = False
        dry_run = False
        verbose = 0
        json = True

    stdout_cap = io.StringIO()
    stderr_cap = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', stdout_cap)
    monkeypatch.setattr(sys, 'stderr', stderr_cap)

    cmd_backup(Args())
    
    assert "[debug]" not in stderr_cap.getvalue()
