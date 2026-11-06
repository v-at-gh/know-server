"""Jinja /settings/profile shows pruned_favorites notice after POST."""

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
        auth.create_user("testadmin", "password123", ["linux", "docker"], ["en"])
    except Exception:
        pass
    c = TestClient(app)
    c.headers.update({"Origin": "http://testserver"})
    c.post("/login", data={"username": "testadmin", "password": "password123"})
    return c


def test_jinja_post_profile_redirects_without_pruned(client):
    r = client.post("/settings/profile", data={
        "domains": ["linux", "docker"],
        "langs": ["en"],
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/settings/profile"


def test_jinja_post_profile_redirects_with_pruned_when_favorites_orphaned(client):
    r = client.put("/api/v1/users/me/settings", json={
        "active_domains": ["linux", "docker"],
        "favorites": ["linux", "docker"],
    })
    assert r.status_code == 200
    r = client.post("/settings/profile", data={
        "domains": ["linux"],
        "langs": ["en"],
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "?pruned=" in r.headers["location"]
    assert "docker" in r.headers["location"]


def test_legacy_put_preserves_favorites(client):
    client.put("/api/v1/users/me/settings", json={
        "active_domains": ["linux"], "favorites": ["linux"]
    })
    client.put("/api/v1/users/me/generation-params", json={"articles_per_domain": 15})
    r = client.get("/api/v1/users/me/settings")
    assert r.json()["favorites"] == ["linux"]


def test_jinja_get_profile_shows_pruned_notice(client):
    r = client.get("/settings/profile?pruned=docker")
    assert r.status_code == 200
    assert "Favorites auto-removed" in r.text
    assert "pruned" in r.text.lower() or "removed" in r.text.lower()


def test_jinja_get_profile_no_notice_when_no_pruned(client):
    r = client.get("/settings/profile")
    assert r.status_code == 200
    assert "Favorites auto-removed" not in r.text
