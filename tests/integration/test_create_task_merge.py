"""Verify create_task applies 5-tier merge and derives username from token."""

import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Orchestrator TestClient with isolated users.db and tasks.db."""
    from quizkb import users as kb_users
    monkeypatch.setattr(kb_users, "USERS_DB_PATH", tmp_path / "users.db")
    if hasattr(kb_users, "_users_db_path"):
        monkeypatch.setattr(kb_users, "_users_db_path", lambda: tmp_path / "users.db")
    kb_users.ensure_schema()

    # Isolate tasks.db too
    monkeypatch.setattr("orchestrator.tasks.DB_PATH", tmp_path / "tasks.db")
    monkeypatch.setattr("orchestrator.tasks.TASKS_DIR", tmp_path / "tasks")

    # Create authenticated user
    from web import auth
    try:
        auth.create_user("alice", "password123", ["linux"], ["en"])
    except Exception:
        pass
    user = auth.authenticate("alice", "password123")
    token = auth.create_api_token(user.id)

    from orchestrator.app import app
    from orchestrator.tasks import TaskManager
    app.state.task_manager = TaskManager()
    app.state.test_token = token
    app.state.test_user_id = user.id

    return TestClient(app)


@pytest.fixture
def headers(client):
    from orchestrator.app import app
    return {"Authorization": f"Bearer {app.state.test_token}"}


def _fetch_task_gp(client, task_id):
    """Read generation_params from tasks.db for a given task."""
    from orchestrator.app import app
    tm = app.state.task_manager
    with tm._get_conn() as conn:
        row = conn.execute(
            "SELECT generation_params, username FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    return json.loads(row["generation_params"]) if row["generation_params"] else None, row["username"]


def test_create_task_resolves_user_global_defaults(client, headers):
    """POST with no body.generation_params → task gets defaults merged for user."""
    r = client.post("/api/v1/tasks", headers=headers, json={
        "action": "bootstrap",
        "domain": "linux",
        "lang": "en",
    })
    assert r.status_code == 201
    task_id = r.json()["task_id"]
    gp, _ = _fetch_task_gp(client, task_id)
    assert gp is not None
    assert gp["articles_per_domain"] == 10
    assert gp["questions_types"] == {"select": 5, "construct": 0}


def test_create_task_applies_per_domain(client, headers):
    """User per_domain[linux] applied when body.domain=linux."""
    from quizkb import users as kb_users
    from orchestrator.app import app

    kb_users.update_user_settings(
        app.state.test_user_id,
        {"generation_params": {"per_domain": {"linux": {"articles_per_domain": 20}}}},
    )

    r = client.post("/api/v1/tasks", headers=headers, json={
        "action": "bootstrap",
        "domain": "linux",
        "lang": "en",
    })
    assert r.status_code == 201
    gp, _ = _fetch_task_gp(client, r.json()["task_id"])
    assert gp["articles_per_domain"] == 20

    # Non-linux domain uses global
    r = client.post("/api/v1/tasks", headers=headers, json={
        "action": "bootstrap",
        "domain": "docker",
        "lang": "en",
    })
    assert r.status_code == 201
    gp, _ = _fetch_task_gp(client, r.json()["task_id"])
    assert gp["articles_per_domain"] == 10


def test_create_task_request_override_wins(client, headers):
    """body.generation_params overrides user settings and per_domain."""
    from quizkb import users as kb_users
    from orchestrator.app import app

    kb_users.update_user_settings(
        app.state.test_user_id,
        {"generation_params": {
            "articles_per_domain": 15,
            "per_domain": {"linux": {"articles_per_domain": 20}},
        }},
    )

    r = client.post("/api/v1/tasks", headers=headers, json={
        "action": "bootstrap",
        "domain": "linux",
        "lang": "en",
        "generation_params": {"articles_per_domain": 25},
    })
    assert r.status_code == 201
    gp, _ = _fetch_task_gp(client, r.json()["task_id"])
    assert gp["articles_per_domain"] == 25


def test_create_task_username_from_token(client, headers):
    """username derived from users.db, not body.username."""
    r = client.post("/api/v1/tasks", headers=headers, json={
        "action": "bootstrap",
        "domain": "linux",
        "lang": "en",
        "username": "fakeadmin",
    })
    assert r.status_code == 201
    _, username = _fetch_task_gp(client, r.json()["task_id"])
    assert username == "alice"


def test_create_task_username_default_ignored(client, headers):
    """Even without body.username, resolved from DB."""
    r = client.post("/api/v1/tasks", headers=headers, json={
        "action": "bootstrap",
        "domain": "linux",
        "lang": "en",
    })
    assert r.status_code == 201
    _, username = _fetch_task_gp(client, r.json()["task_id"])
    assert username == "alice"


def test_create_task_unauthenticated_rejected(client):
    """No token → 401, no task created."""
    r = client.post("/api/v1/tasks", json={
        "action": "bootstrap", "domain": "linux", "lang": "en",
    })
    assert r.status_code == 401


def test_web_payload_helper_returns_none():
    """_get_user_generation_payload deprecated — always returns None."""
    from web.app import _get_user_generation_payload
    assert _get_user_generation_payload(None) is None
    assert _get_user_generation_payload(object()) is None


def test_direct_enqueue_still_accepts_generation_params(client):
    """Backward compat: direct tm.enqueue(generation_params={...}) still works."""
    from orchestrator.app import app

    tm = app.state.task_manager
    tid = tm.enqueue(
        action="bootstrap",
        domain="linux",
        lang="en",
        generation_params={"articles_per_domain": 7, "questions_types": {"select": 3, "construct": 1}},
    )
    with tm._get_conn() as conn:
        row = conn.execute(
            "SELECT generation_params FROM tasks WHERE id = ?", (tid,)
        ).fetchone()
    gp = json.loads(row["generation_params"])
    assert gp["articles_per_domain"] == 7
    assert gp["questions_types"] == {"select": 3, "construct": 1}
