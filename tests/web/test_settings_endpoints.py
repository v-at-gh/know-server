"""Endpoint tests for /api/v1/users/me/settings (GET, PUT)."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Authenticated client with isolated users.db and app.db."""
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
        auth.create_user("testadmin", "password123", ["linux"], ["en"])
    except Exception:
        pass

    c = TestClient(app)
    c.headers.update({"Origin": "http://testserver"})
    c.post("/login", data={"username": "testadmin", "password": "password123"})
    return c


def test_get_settings_full_shape(client):
    r = client.get("/api/v1/users/me/settings")
    assert r.status_code == 200
    data = r.json()
    assert "profile" in data
    assert data["profile"]["username"] == "testadmin"
    assert data["profile"]["active_domains"] == ["linux"]
    assert data["profile"]["active_langs"] == ["en"]
    assert data["generation_params"]["articles_per_domain"] == 10
    assert data["generation_params"]["questions_types"] == {"select": 5, "construct": 0}
    assert data["favorites"] == []
    assert "pruned_favorites" not in data


def test_put_settings_partial_active_only(client):
    r = client.put("/api/v1/users/me/settings", json={"active_domains": ["linux", "docker"]})
    assert r.status_code == 200
    data = r.json()
    assert data["profile"]["active_domains"] == ["linux", "docker"]
    assert data["generation_params"]["articles_per_domain"] == 10


def test_put_settings_partial_gp_only(client):
    r = client.put("/api/v1/users/me/settings", json={
        "generation_params": {"articles_per_domain": 15}
    })
    assert r.status_code == 200
    assert r.json()["generation_params"]["articles_per_domain"] == 15
    assert r.json()["profile"]["active_domains"] == ["linux"]


def test_put_settings_invalid_articles_returns_400(client):
    r = client.put("/api/v1/users/me/settings", json={
        "generation_params": {"articles_per_domain": 100}
    })
    assert r.status_code == 400
    assert "articles_per_domain" in r.json()["detail"]


def test_put_settings_explicit_favorites_not_in_active_returns_400(client):
    r = client.put("/api/v1/users/me/settings", json={"favorites": ["docker"]})
    assert r.status_code == 400
    assert "favorites not in active_domains" in r.json()["detail"]


def test_put_settings_implicit_prune_returns_pruned_favorites(client):
    r = client.put("/api/v1/users/me/settings", json={
        "active_domains": ["linux", "docker"],
        "favorites": ["linux", "docker"],
    })
    assert r.status_code == 200
    r = client.put("/api/v1/users/me/settings", json={"active_domains": ["linux"]})
    assert r.status_code == 200
    data = r.json()
    assert data["favorites"] == ["linux"]
    assert data["pruned_favorites"] == ["docker"]


def test_put_settings_full_update(client):
    r = client.put("/api/v1/users/me/settings", json={
        "active_domains": ["linux", "docker"],
        "active_langs": ["en", "ru"],
        "generation_params": {"articles_per_domain": 20, "questions_types": {"select": 3, "construct": 2}},
        "favorites": ["linux"],
    })
    assert r.status_code == 200
    data = r.json()
    assert data["profile"]["active_domains"] == ["linux", "docker"]
    assert data["profile"]["active_langs"] == ["en", "ru"]
    assert data["generation_params"]["articles_per_domain"] == 20
    assert data["generation_params"]["questions_types"] == {"select": 3, "construct": 2}
    assert data["favorites"] == ["linux"]


def test_put_settings_preserves_per_domain_on_partial_gp_update(client):
    r = client.put("/api/v1/users/me/settings", json={
        "generation_params": {"per_domain": {"linux": {"articles_per_domain": 20}}}
    })
    assert r.status_code == 200
    r = client.put("/api/v1/users/me/settings", json={
        "generation_params": {"articles_per_domain": 15}
    })
    assert r.status_code == 200
    data = r.json()
    assert data["generation_params"]["articles_per_domain"] == 15
    assert data["generation_params"]["per_domain"]["linux"]["articles_per_domain"] == 20
