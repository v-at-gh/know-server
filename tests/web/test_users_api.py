"""HTTP endpoint tests for /api/v1/users/*."""

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
        auth.create_user("admin", "adminpass123", ["A"], ["en"])
    except Exception:
        pass
    admin = auth.authenticate("admin", "adminpass123")
    admin_token = auth.create_api_token(admin.id)

    try:
        auth.create_user("alice", "alicepass123", ["B"], ["en"])
    except Exception:
        pass
    alice = auth.authenticate("alice", "alicepass123")
    alice_token = auth.create_api_token(alice.id)

    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {admin_token}"})
    c._alice_token = alice_token
    return c


def test_list_users_admin(client):
    r = client.get("/api/v1/users")
    assert r.status_code == 200
    users = r.json()["users"]
    usernames = {u["username"] for u in users}
    assert "admin" in usernames
    assert "alice" in usernames


def test_list_users_non_admin_403(client):
    c = TestClient(client.app)
    c.headers.update({"Authorization": f"Bearer {client._alice_token}"})
    r = c.get("/api/v1/users")
    assert r.status_code == 403


def test_create_user(client):
    r = client.post("/api/v1/users", json={
        "username": "bob", "password": "bobpass123",
        "active_domains": ["C"], "active_langs": ["ru"],
    })
    assert r.status_code == 201
    assert r.json()["username"] == "bob"


def test_create_user_duplicate_409(client):
    r = client.post("/api/v1/users", json={
        "username": "alice", "password": "different123",
    })
    assert r.status_code == 409


def test_create_user_short_password_400(client):
    r = client.post("/api/v1/users", json={
        "username": "shortpw", "password": "abc",
    })
    assert r.status_code == 400


def test_delete_self_400(client):
    r = client.get("/api/v1/users")
    admin = next(u for u in r.json()["users"] if u["username"] == "admin")
    r = client.delete(f"/api/v1/users/{admin['id']}")
    assert r.status_code == 400
    assert "cannot delete self" in r.json()["detail"]


def test_delete_user_ok(client):
    r = client.get("/api/v1/users")
    alice = next(u for u in r.json()["users"] if u["username"] == "alice")
    r = client.delete(f"/api/v1/users/{alice['id']}")
    assert r.status_code == 200
    r = client.get(f"/api/v1/users/{alice['id']}")
    assert r.status_code == 404


def test_delete_service_account_403(client, monkeypatch):
    from web import auth
    try:
        auth.create_user("sys_test", "syspass123", [], [])
    except Exception:
        pass
    u = auth.get_user_by_username("sys_test")
    r = client.delete(f"/api/v1/users/{u.id}")
    assert r.status_code == 403


def test_self_change_password(client):
    c = TestClient(client.app)
    c.headers.update({"Authorization": f"Bearer {client._alice_token}"})
    r = c.patch("/api/v1/users/me/password", json={
        "old_password": "alicepass123", "new_password": "newalice456",
    })
    assert r.status_code == 200
    from web import auth
    assert auth.authenticate("alice", "alicepass123") is None
    assert auth.authenticate("alice", "newalice456") is not None


def test_self_change_password_wrong_old(client):
    c = TestClient(client.app)
    c.headers.update({"Authorization": f"Bearer {client._alice_token}"})
    r = c.patch("/api/v1/users/me/password", json={
        "old_password": "wrong", "new_password": "newalice456",
    })
    assert r.status_code == 401


def test_admin_override_password(client):
    r = client.get("/api/v1/users")
    alice = next(u for u in r.json()["users"] if u["username"] == "alice")
    r = client.patch(f"/api/v1/users/{alice['id']}/password", json={
        "new_password": "override789",
    })
    assert r.status_code == 200
    from web import auth
    assert auth.authenticate("alice", "override789") is not None


def test_token_list_and_revoke(client):
    c = TestClient(client.app)
    c.headers.update({"Authorization": f"Bearer {client._alice_token}"})
    r = c.get("/api/v1/users/me/tokens")
    assert r.status_code == 200
    tokens = r.json()["tokens"]
    assert len(tokens) >= 1

    r = c.post("/api/v1/users/me/tokens")
    assert r.status_code == 201
    new_token = r.json()["token"]

    r = c.delete(f"/api/v1/users/me/tokens/{new_token}")
    assert r.status_code == 200


def test_revoke_other_user_token_404(client):
    r = client.delete(f"/api/v1/users/me/tokens/{client._alice_token}")
    assert r.status_code == 404
