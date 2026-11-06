import os
import sqlite3
import json
import sys
import io
from pathlib import Path
import pytest
from scripts.knowctl import (
    _backup_db,
    read_jsonl_file,
    find_records_by_task_id,
    cmd_backup,
    cmd_integrity,
    cmd_tasks_list,
)

def test_backup_db_helper(tmp_path):
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    with sqlite3.connect(src) as conn:
        conn.execute("CREATE TABLE test (id INT)")
        conn.execute("INSERT INTO test VALUES (1)")
        conn.commit()

    _backup_db(src, dst)
    assert dst.exists()
    with sqlite3.connect(dst) as conn:
        res = conn.execute("PRAGMA quick_check;").fetchone()
        assert res[0] == "ok"
        val = conn.execute("SELECT id FROM test").fetchone()
        assert val[0] == 1

def test_backup_no_subprocess(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    backups_dir = state_dir / "backups"
    state_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)

    content_db = state_dir / "content.db"
    with sqlite3.connect(content_db) as conn:
        conn.execute("CREATE TABLE test (id INT)")
        conn.commit()

    monkeypatch.setattr("scripts.knowctl.REPO_ROOT", tmp_path)
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(Exception("Should not use subprocess")))

    class Args:
        content_only = True
        output_only = False
        state_only = False
        dry_run = False
        verbose = 0
        json = True

    cmd_backup(Args())
    bak_files = list(backups_dir.glob("content.db.*.bak"))
    assert len(bak_files) == 1

def test_tasks_list_orphaned_limit(tmp_path, monkeypatch):
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
        conn.execute("INSERT INTO tasks VALUES ('t1', 'bootstrap', 'dom', 'en', 'running', 1, 'system', '2026-10-26T00:00:01')")
        conn.execute("INSERT INTO tasks VALUES ('t2', 'bootstrap', 'dom', 'en', 'running', 2, 'system', '2026-10-26T00:00:02')")
        conn.execute("INSERT INTO tasks VALUES ('t3', 'bootstrap', 'dom', 'en', 'running', 3, 'system', '2026-10-26T00:00:03')") # orphaned
        conn.execute("INSERT INTO tasks VALUES ('t4', 'bootstrap', 'dom', 'en', 'running', 4, 'system', '2026-10-26T00:00:04')")
        conn.execute("INSERT INTO tasks VALUES ('t5', 'bootstrap', 'dom', 'en', 'running', 5, 'system', '2026-10-26T00:00:05')")
        conn.commit()

    monkeypatch.setattr("scripts.knowctl.REPO_ROOT", tmp_path)
    monkeypatch.setattr("scripts.knowctl._is_orphaned", lambda t: t["id"] == "t3")
    
    class Args:
        status = None
        orphaned = True
        limit = 1
        json = True

    captured = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', captured)
    cmd_tasks_list(Args())
    data = json.loads(captured.getvalue())
    assert len(data) == 1
    assert data[0]["id"] == "t3"
    assert data[0]["orphaned"] is True

def test_integrity_questions_parity(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    content_db = state_dir / "content.db"

    with sqlite3.connect(content_db) as conn:
        conn.execute("CREATE TABLE articles (id TEXT)")
        conn.execute("CREATE TABLE article_translations (article_id TEXT)")
        conn.execute("CREATE TABLE questions (id TEXT)")
        conn.execute("CREATE TABLE question_translations (id TEXT)")
        conn.execute("CREATE VIRTUAL TABLE articles_fts USING fts5(lang, article_id, title, summary, content)")
        conn.execute("CREATE VIRTUAL TABLE questions_fts USING fts5(lang, question_id, text, answer, hints)")

        conn.execute("INSERT INTO question_translations VALUES ('q1')")
        conn.execute("INSERT INTO questions_fts VALUES ('en', 'q1', 't', 'a', 'h')")
        conn.execute("INSERT INTO questions_fts VALUES ('en', 'q2', 't2', 'a2', 'h2')")
        conn.commit()

    monkeypatch.setattr("scripts.knowctl.REPO_ROOT", tmp_path)

    class Args:
        json = True

    captured = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', captured)
    with pytest.raises(SystemExit) as exc:
        cmd_integrity(Args())
    assert exc.value.code == 1
    data = json.loads(captured.getvalue())
    parity_check = next(c for c in data["checks"] if c["name"] == "fts_questions_parity")
    assert parity_check["status"] == "FAIL"

def test_integrity_empty_content_db_skips(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    content_db = state_dir / "content.db"

    with sqlite3.connect(content_db) as conn:
        conn.execute("CREATE TABLE articles (id TEXT)")
        conn.execute("CREATE TABLE article_translations (article_id TEXT)")
        conn.execute("CREATE TABLE questions (id TEXT)")
        conn.execute("CREATE TABLE question_translations (id TEXT)")
        conn.execute("CREATE VIRTUAL TABLE articles_fts USING fts5(lang, article_id, title, summary, content)")
        conn.execute("CREATE VIRTUAL TABLE questions_fts USING fts5(lang, question_id, text, answer, hints)")
        conn.commit()

    monkeypatch.setattr("scripts.knowctl.REPO_ROOT", tmp_path)

    class Args:
        json = True

    captured = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', captured)
    with pytest.raises(SystemExit) as exc:
        cmd_integrity(Args())
    assert exc.value.code == 0
    data = json.loads(captured.getvalue())
    state_check = next(c for c in data["checks"] if c["name"] == "content_state")
    assert state_check["status"] == "SKIP"

def test_logs_reads_rotated_files(tmp_path):
    log_path = tmp_path / "worker.jsonl"
    log_path_1 = tmp_path / "worker.jsonl.1"

    with log_path_1.open("w", encoding="utf-8") as f:
        for i in range(30):
            f.write(json.dumps({"level": "info", "message": f"rot {i}"}) + "\n")

    with log_path.open("w", encoding="utf-8") as f:
        for i in range(5):
            f.write(json.dumps({"level": "info", "message": f"cur {i}"}) + "\n")

    recs = read_jsonl_file(log_path, 20, "debug")
    assert len(recs) == 20
    assert recs[0]["message"] == "rot 15"
    assert recs[-1]["message"] == "cur 4"

def test_logs_task_search_beyond_1000(tmp_path):
    log_path = tmp_path / "worker.jsonl"
    
    with log_path.open("w", encoding="utf-8") as f:
        for i in range(1500):
            tid = "target_task" if i in (5, 1200) else f"other_{i}"
            f.write(json.dumps({"level": "info", "task_id": tid, "message": f"msg {i}"}) + "\n")

    recs = find_records_by_task_id(log_path, "target_task", 10, "debug")
    assert len(recs) == 2
    assert recs[0]["message"] == "msg 5"
    assert recs[1]["message"] == "msg 1200"
