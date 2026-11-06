import json
import pytest
from fastapi.testclient import TestClient
from orchestrator.app import app
from web import auth

@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    db_file = tmp_path / "tasks.db"
    monkeypatch.setattr("orchestrator.tasks.DB_PATH", db_file)
    auth.init_db()
    auth.create_user("testadmin", "password123", ["linux"], ["en"])
    user = auth.authenticate("testadmin", "password123")
    token = auth.create_api_token(user.id)
    app.state.test_token = token
    from orchestrator.tasks import TaskManager
    app.state.task_manager = TaskManager()

def test_schema_migration_adds_kind_priority():
    tm = app.state.task_manager
    with tm._get_conn() as conn:
        row = conn.execute("PRAGMA table_info(tasks)").fetchall()
        cols = {r["name"] for r in row}
        assert "kind" in cols
        assert "priority" in cols

def test_enqueue_priority_resolution():
    tm = app.state.task_manager
    tid1 = tm.enqueue(action="reindex_missing", domain="", lang="", kind="reindex")
    tid2 = tm.enqueue(action="reindex_full", domain="", lang="", kind="reindex")
    tid3 = tm.enqueue(action="generate", domain="linux", lang="en", kind="generation")

    assert tm.get_task(tid1)["priority"] == 10
    assert tm.get_task(tid2)["priority"] == -10
    assert tm.get_task(tid3)["priority"] == 0

def test_claim_priority_order():
    tm = app.state.task_manager
    # Enqueue low priority (-10), then normal (0), then high (20)
    tid_low = tm.enqueue(action="reindex_full", kind="reindex")
    tid_norm = tm.enqueue(action="generate", domain="linux", lang="en")
    tid_high = tm.enqueue(action="reindex_domain", domain="linux", kind="reindex")

    claimed1 = tm.claim_next_task()
    assert claimed1["id"] == tid_high
    assert claimed1["priority"] == 20

    claimed2 = tm.claim_next_task()
    assert claimed2["id"] == tid_norm
    assert claimed2["priority"] == 0

    claimed3 = tm.claim_next_task()
    assert claimed3["id"] == tid_low
    assert claimed3["priority"] == -10

def test_dedup_queued_scope():
    tm = app.state.task_manager
    tid1 = tm.enqueue(action="reindex_missing", kind="reindex", dedup_scope="queued")
    # Claim it so it's running
    claimed = tm.claim_next_task()
    assert claimed["id"] == tid1
    assert claimed["status"] == "running"

    # Enqueue another reindex_missing while first is running -> should NOT dedup (queued scope only)
    tid2 = tm.enqueue(action="reindex_missing", kind="reindex", dedup_scope="queued")
    assert tid1 != tid2

    # Now enqueue a third while second is queued -> should dedup to tid2
    tid3 = tm.enqueue(action="reindex_missing", kind="reindex", dedup_scope="queued")
    assert tid2 == tid3

def test_restart_preserves_kind_priority():
    tm = app.state.task_manager
    tid = tm.enqueue(action="reindex_missing", kind="reindex")
    new_id = tm.restart(tid, wait_for_exit=False)
    new_task = tm.get_task(new_id)
    assert new_task["kind"] == "reindex"
    assert new_task["priority"] == 10

def test_api_kind_validation():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {app.state.test_token}"}
    res = client.post("/api/v1/tasks", headers=headers, json={
        "kind": "invalid",
        "action": "generate",
        "domain": "linux",
        "lang": "en"
    })
    assert res.status_code == 400

def test_api_reindex_domain_requires_domain():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {app.state.test_token}"}
    res = client.post("/api/v1/tasks", headers=headers, json={
        "kind": "reindex",
        "action": "reindex_domain",
        "domain": "",
        "lang": "en"
    })
    assert res.status_code == 400

def test_api_generation_requires_domain_lang():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {app.state.test_token}"}
    res = client.post("/api/v1/tasks", headers=headers, json={
        "kind": "generation",
        "action": "generate",
        "domain": "",
        "lang": ""
    })
    assert res.status_code == 400

def test_api_reindex_priority_ignored_from_client():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {app.state.test_token}"}
    res = client.post("/api/v1/tasks", headers=headers, json={
        "kind": "reindex",
        "action": "reindex_missing",
        "priority": 999
    })
    assert res.status_code == 201
    task_id = res.json()["task_id"]
    task = app.state.task_manager.get_task(task_id)
    assert task["priority"] == 10  # resolved from config, not 999
