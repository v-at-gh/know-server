import pytest
import sqlite3
from pathlib import Path
from orchestrator.tasks import TaskManager
import orchestrator.tasks as tasks_module


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    db_file = tmp_path / "tasks.db"
    monkeypatch.setattr("orchestrator.tasks.DB_PATH", db_file)


def test_flag_false_no_enqueue(monkeypatch):
    monkeypatch.setattr(tasks_module, "AUTO_REINDEX_AFTER_GENERATE", False)
    tm = TaskManager()
    tid = tm.enqueue(action="bootstrap", domain="linux", lang="en", kind="generation")
    task = tm.claim_next_task()
    assert task["id"] == tid

    tm.update_task_status(tid, "done")

    all_tasks, _ = tm.list_tasks()
    reindex_tasks = [t for t in all_tasks if t["kind"] == "reindex"]
    assert len(reindex_tasks) == 0


def test_flag_true_generation_done_enqueues(monkeypatch):
    monkeypatch.setattr(tasks_module, "AUTO_REINDEX_AFTER_GENERATE", True)
    tm = TaskManager()
    tid = tm.enqueue(action="bootstrap", domain="linux", lang="en", kind="generation")
    task = tm.claim_next_task()
    assert task["id"] == tid

    tm.update_task_status(tid, "done")

    all_tasks, _ = tm.list_tasks()
    reindex_tasks = [t for t in all_tasks if t["kind"] == "reindex"]
    assert len(reindex_tasks) == 1
    r_task = reindex_tasks[0]
    assert r_task["action"] == "reindex_missing"
    assert r_task["kind"] == "reindex"
    assert r_task["priority"] == 10
    assert r_task["status"] == "queued"


def test_flag_true_generation_error_no_enqueue(monkeypatch):
    monkeypatch.setattr(tasks_module, "AUTO_REINDEX_AFTER_GENERATE", True)
    tm = TaskManager()
    tid = tm.enqueue(action="bootstrap", domain="linux", lang="en", kind="generation")
    task = tm.claim_next_task()

    tm.update_task_status(tid, "error", error_message="boom")

    all_tasks, _ = tm.list_tasks()
    reindex_tasks = [t for t in all_tasks if t["kind"] == "reindex"]
    assert len(reindex_tasks) == 0


def test_flag_true_reindex_done_no_recursion(monkeypatch):
    monkeypatch.setattr(tasks_module, "AUTO_REINDEX_AFTER_GENERATE", True)
    tm = TaskManager()
    tid = tm.enqueue(action="reindex_missing", kind="reindex")
    task = tm.claim_next_task()

    tm.update_task_status(tid, "done")

    all_tasks, _ = tm.list_tasks()
    reindex_tasks = [t for t in all_tasks if t["kind"] == "reindex"]
    assert len(reindex_tasks) == 1  # Only the original reindex task, no new ones spawned


def test_flag_true_coalesce_queued(monkeypatch):
    monkeypatch.setattr(tasks_module, "AUTO_REINDEX_AFTER_GENERATE", True)
    tm = TaskManager()
    tid1 = tm.enqueue(action="bootstrap", domain="linux", lang="en", kind="generation")
    tid2 = tm.enqueue(action="bootstrap", domain="docker", lang="en", kind="generation")

    tm.update_task_status(tid1, "done")
    tm.update_task_status(tid2, "done")

    all_tasks, _ = tm.list_tasks()
    queued_reindex = [t for t in all_tasks if t["kind"] == "reindex" and t["status"] == "queued"]
    assert len(queued_reindex) == 1


def test_double_done_no_duplicate_enqueue(monkeypatch):
    monkeypatch.setattr(tasks_module, "AUTO_REINDEX_AFTER_GENERATE", True)
    tm = TaskManager()
    tid = tm.enqueue(action="bootstrap", domain="linux", lang="en", kind="generation")
    task = tm.claim_next_task()

    tm.update_task_status(tid, "done")
    tm.update_task_status(tid, "done")  # Retry

    all_tasks, _ = tm.list_tasks()
    reindex_tasks = [t for t in all_tasks if t["kind"] == "reindex"]
    assert len(reindex_tasks) == 1


def test_flag_true_interrupted_no_enqueue(monkeypatch):
    monkeypatch.setattr(tasks_module, "AUTO_REINDEX_AFTER_GENERATE", True)
    tm = TaskManager()
    tid = tm.enqueue(action="bootstrap", domain="linux", lang="en", kind="generation")
    task = tm.claim_next_task()

    tm.update_task_status(tid, "interrupted")

    all_tasks, _ = tm.list_tasks()
    reindex_tasks = [t for t in all_tasks if t["kind"] == "reindex"]
    assert len(reindex_tasks) == 0


def test_queued_scope_running_does_not_block(monkeypatch):
    monkeypatch.setattr(tasks_module, "AUTO_REINDEX_AFTER_GENERATE", True)
    tm = TaskManager()
    # Enqueue and claim a reindex task so one is running
    reindex_tid = tm.enqueue(action="reindex_missing", kind="reindex")
    running_reindex = tm.claim_next_task()
    assert running_reindex["id"] == reindex_tid
    assert running_reindex["status"] == "running"

    # Now run and complete a generation task
    gen_tid = tm.enqueue(action="bootstrap", domain="linux", lang="en", kind="generation")
    gen_task = tm.claim_next_task()
    tm.update_task_status(gen_task["id"], "done")

    all_tasks, _ = tm.list_tasks()
    reindex_tasks = [t for t in all_tasks if t["kind"] == "reindex"]
    # Should have 1 running (the first one) and 1 queued (auto-triggered)
    assert len(reindex_tasks) == 2
    statuses = sorted([t["status"] for t in reindex_tasks])
    assert statuses == ["queued", "running"]
