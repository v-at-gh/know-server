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

def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "active_tasks" in data

def test_tasks_auth_required():
    client = TestClient(app)
    response = client.post("/api/v1/tasks", json={
        "action": "bootstrap",
        "domain": "linux",
        "lang": "en"
    })
    assert response.status_code == 401

def test_task_lifecycle():
    client = TestClient(app)
    token = app.state.test_token
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Enqueue task via Client API with force=True
    res = client.post("/api/v1/tasks", headers=headers, json={
        "action": "bootstrap",
        "domain": "linux",
        "lang": "en",
        "username": "testadmin",
        "force": True
    })
    assert res.status_code == 201
    task_data = res.json()
    task_id = task_data["task_id"]
    assert task_data["status"] == "queued"

    # 2. Worker polls for task
    poll_res = client.get("/api/v1/workers/poll")
    assert poll_res.status_code == 200
    polled = poll_res.json()["task"]
    assert polled["id"] == task_id
    assert polled["status"] == "running"

    # 3. Worker streams logs
    log_res = client.post(f"/api/v1/workers/{task_id}/log", data={"lines": "[INFO] Generating content..."})
    assert log_res.status_code == 200

    # 4. Client reads task log
    client_log_res = client.get(f"/api/v1/tasks/{task_id}/log", headers=headers)
    assert client_log_res.status_code == 200
    assert "Generating content" in client_log_res.json()["log"]

    # 5. Worker reports completion (progress)
    prog_res = client.post(f"/api/v1/workers/{task_id}/progress", data={"status": "done", "pid": 1234})
    assert prog_res.status_code == 200

    # 6. Client checks task detail
    detail_res = client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    assert detail_res.status_code == 200
    assert detail_res.json()["status"] == "done"

def test_create_task_persists_generation_params():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {app.state.test_token}"}
    res = client.post("/api/v1/tasks", headers=headers, json={
        "action": "bootstrap",
        "domain": "linux",
        "lang": "en",
        "username": "testadmin",
        "force": True,
        "generation_params": {
            "articles_per_domain": 4,
            "questions_types": {"select": 2, "construct": 1},
        },
    })
    assert res.status_code == 201
    task_id = res.json()["task_id"]

    # Verify persisted JSON matches what was sent
    tm = app.state.task_manager
    with tm._get_conn() as conn:
        row = conn.execute(
            "SELECT generation_params FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    assert row is not None
    gp = json.loads(row["generation_params"])
    assert gp["articles_per_domain"] == 4
    assert gp["questions_types"] == {"select": 2, "construct": 1}

    # Verify worker poll returns the same data
    poll_res = client.get("/api/v1/workers/poll")
    assert poll_res.status_code == 200
    polled = poll_res.json()["task"]
    assert polled["generation_params"] is not None
