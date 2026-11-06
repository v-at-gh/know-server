"""Backward-compat tests: legacy endpoints return identical shape to pre-Cycle-B."""

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
        auth.create_user("testadmin", "password123", ["linux"], ["en"])
    except Exception:
        pass
    c = TestClient(app)
    c.headers.update({"Origin": "http://testserver"})
    c.post("/login", data={"username": "testadmin", "password": "password123"})
    return c


def test_legacy_get_shape_locked(client):
    r = client.get("/api/v1/users/me/generation-params")
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"articles_per_domain", "questions_types"}
    assert "per_domain" not in data
    assert "per_lang" not in data
    assert "favorites" not in data
    assert "pruned_favorites" not in data


def test_legacy_put_shape_locked(client):
    r = client.put("/api/v1/users/me/generation-params", json={"articles_per_domain": 15})
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"articles_per_domain", "questions_types"}
    assert data["articles_per_domain"] == 15


def test_legacy_put_partial_questions(client):
    r = client.put("/api/v1/users/me/generation-params", json={"questions_types": {"construct": 2}})
    assert r.status_code == 200
    data = r.json()
    assert data["questions_types"]["select"] == 5
    assert data["questions_types"]["construct"] == 2


def test_legacy_put_still_validates_400(client):
    r = client.put("/api/v1/users/me/generation-params", json={"articles_per_domain": 100})
    assert r.status_code == 400
    assert "articles_per_domain" in r.json()["detail"]


def test_legacy_does_not_wipe_per_domain_set_via_new_api(client):
    r = client.put("/api/v1/users/me/settings", json={
        "generation_params": {"per_domain": {"linux": {"articles_per_domain": 20}}}
    })
    assert r.status_code == 200
    r = client.put("/api/v1/users/me/generation-params", json={"articles_per_domain": 12})
    assert r.status_code == 200
    r = client.get("/api/v1/users/me/settings")
    assert r.json()["generation_params"]["per_domain"]["linux"]["articles_per_domain"] == 20
