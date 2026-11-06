"""Contract tests: CLI payload shape matches what backend accepts."""

import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Backend with isolated users.db."""
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
        auth.create_user("alice", "pw", ["linux", "docker"], ["en", "ru"])
    except Exception:
        pass

    client = TestClient(app)
    client.headers.update({"Origin": "http://testserver"})
    client.post("/login", data={"username": "alice", "password": "pw"})
    return client


def test_cli_set_patch_accepted_by_backend(env):
    """The exact patch shape produced by `know users settings set` must be valid."""
    patch = {
        "generation_params": {
            "articles_per_domain": 15,
            "questions_types": {"select": 3, "construct": 2},
            "per_domain": {"linux": {"questions_types": {"select": 10}}},
            "per_lang": {"ru": {"translate_domains": "*"}},
        },
        "active_domains": ["linux", "docker"],
        "favorites": ["linux"],
    }
    r = env.put("/api/v1/users/me/settings", json=patch)
    assert r.status_code == 200, f"Backend rejected CLI-shaped patch: {r.text}"
    data = r.json()
    assert data["generation_params"]["articles_per_domain"] == 15
    assert data["generation_params"]["per_domain"]["linux"]["questions_types"]["select"] == 10
    assert data["generation_params"]["per_lang"]["ru"]["translate_domains"] == "*"


def test_export_reimport_roundtrip(env):
    """Export → import → same state."""
    patch = {
        "active_domains": ["linux", "docker"],
        "active_langs": ["en", "ru"],
        "generation_params": {
            "articles_per_domain": 12,
            "questions_types": {"select": 4, "construct": 1},
            "per_domain": {"docker": {"articles_per_domain": 20}},
        },
        "favorites": ["linux"],
    }
    r = env.put("/api/v1/users/me/settings", json=patch)
    assert r.status_code == 200

    settings = env.get("/api/v1/users/me/settings").json()
    exported = {
        "active_domains": settings["profile"]["active_domains"],
        "active_langs": settings["profile"]["active_langs"],
        "generation_params": settings["generation_params"],
        "favorites": settings["favorites"],
    }

    env.put("/api/v1/users/me/settings", json={"active_domains": [], "active_langs": [], "favorites": [], "generation_params": {"articles_per_domain": 10, "questions_types": {"select": 5, "construct": 0}}})

    r = env.put("/api/v1/users/me/settings", json=exported)
    assert r.status_code == 200
    after = r.json()
    assert after["generation_params"]["articles_per_domain"] == 12
    assert after["profile"]["active_domains"] == ["linux", "docker"]
    assert after["favorites"] == ["linux"]
