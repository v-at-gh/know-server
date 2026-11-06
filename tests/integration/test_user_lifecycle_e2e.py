"""End-to-end: full user lifecycle via HTTP API only."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from quizkb import users as kb_users
    monkeypatch.setattr(kb_users, "USERS_DB_PATH", tmp_path / "users.db")
    if hasattr(kb_users, "_users_db_path"):
        monkeypatch.setattr(kb_users, "_users_db_path", lambda: tmp_path / "users.db")
    kb_users.ensure_schema()

    from web.app import app
    from web import auth
    from web.db import init_app_db
    init_app_db()
    try:
        auth.create_user("admin", "adminpass123", [], [])
    except Exception:
        pass
    admin = auth.authenticate("admin", "adminpass123")
    token = auth.create_api_token(admin.id)

    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


def test_full_lifecycle(client):
    r = client.post("/api/v1/users", json={
        "username": "lifecycle", "password": "start123",
        "active_domains": ["A"], "active_langs": ["en"],
    })
    assert r.status_code == 201
    user_id = r.json()["id"]

    r = client.post("/api/v1/auth/token", json={
        "username": "lifecycle", "password": "start123",
    })
    assert r.status_code == 200
    user_token = r.json()["token"]

    c_user = TestClient(client.app)
    c_user.headers.update({"Authorization": f"Bearer {user_token}"})
    r = c_user.get("/api/v1/users")
    assert r.status_code == 403

    r = c_user.patch("/api/v1/users/me/password", json={
        "old_password": "start123", "new_password": "changed456",
    })
    assert r.status_code == 200

    r = client.post("/api/v1/auth/token", json={
        "username": "lifecycle", "password": "start123",
    })
    assert r.status_code == 401

    r = client.post("/api/v1/auth/token", json={
        "username": "lifecycle", "password": "changed456",
    })
    assert r.status_code == 200

    r = client.delete(f"/api/v1/users/{user_id}")
    assert r.status_code == 200

    r = client.post("/api/v1/auth/token", json={
        "username": "lifecycle", "password": "changed456",
    })
    assert r.status_code == 401
