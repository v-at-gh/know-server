import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from web.app import app
from web import auth
from web.db import init_app_db, get_app_db
import web.app as app_module


@pytest.fixture
def mock_env(tmp_path: Path, monkeypatch):
    out_dir = tmp_path / "output"
    (out_dir / "datasets" / "en").mkdir(parents=True, exist_ok=True)
    (out_dir / "datasets" / "ru").mkdir(parents=True, exist_ok=True)
    
    monkeypatch.setattr(app_module, "OUTPUT_ROOT", out_dir)
    return out_dir


@pytest.fixture
def client(mock_env, isolated_users_db):
    c = TestClient(app)
    c.headers.update({"Origin": "http://testserver"})
    auth.init_db()
    init_app_db()
    try:
        auth.create_user("testadmin", "password123", ["testdom"], ["en", "ru"])
    except Exception:
        pass
    c.post("/login", data={"username": "testadmin", "password": "password123"})
    
    with get_app_db() as conn:
        conn.execute("INSERT OR REPLACE INTO domains (slug, name, description, kind) VALUES (?, ?, ?, ?)",
                     ("testdom", "Test Domain", "Desc", "pack"))
        conn.execute("INSERT OR REPLACE INTO campaigns (slug, name, description, \"order\") VALUES (?, ?, ?, ?)",
                     ("testcamp", "Test Campaign", "Camp Desc", 1))
        conn.execute("INSERT OR REPLACE INTO campaign_domains (campaign_slug, domain_slug, position) VALUES (?, ?, ?)",
                     ("testcamp", "testdom", 1))
        conn.commit()

    return c


def test_campaign_detail_happy(client, isolated_campaigns):
    response = client.get("/api/v1/campaigns/testcamp?langs=en")
    assert response.status_code == 200
    data = response.json()
    assert data["slug"] == "testcamp"
    assert data["name"] == "Test Campaign"
    assert "cells" in data


def test_campaign_detail_404(client, isolated_campaigns):
    response = client.get("/api/v1/campaigns/nonexistent?langs=en")
    assert response.status_code == 404


def test_campaign_detail_no_state_dependency(client, isolated_campaigns, monkeypatch, tmp_path):
    """Test must not depend on state/config/campaigns.json presence."""
    # Force state/config absent
    import quizkb.config as config_mod
    fake_state = tmp_path / "state"
    fake_state.mkdir()
    monkeypatch.setattr(config_mod, "STATE_DIR", fake_state)
    monkeypatch.setattr(config_mod, "APP_DB_PATH", fake_state / "app.db")
    # isolated_campaigns fixture already patches CAMPAIGNS
    response = client.get("/api/v1/campaigns/testcamp?langs=en")
    assert response.status_code == 200


def test_domain_detail_happy(client):
    response = client.get("/api/v1/domains/testdom?langs=en,ru")
    assert response.status_code == 200
    data = response.json()
    assert data["slug"] == "testdom"
    assert data["name"] == "Test Domain"
    assert "testcamp" in data["campaigns"]
    assert "en" in data["status"]
    assert "ru" in data["status"]


def test_domain_detail_404(client):
    response = client.get("/api/v1/domains/nonexistent?langs=en")
    assert response.status_code == 404


def test_domain_detail_fallback(tmp_path, monkeypatch, isolated_users_db):
    # Create syllabus on disk, do NOT insert into app.db
    out_dir = tmp_path / "output"
    (out_dir / "syllabus" / "en").mkdir(parents=True, exist_ok=True)
    (out_dir / "datasets" / "en").mkdir(parents=True, exist_ok=True)
    (out_dir / "syllabus" / "en" / "orphan-dom.json").write_text(
        json.dumps([{"order": 1, "slug": "a1", "title": "T",
                     "summary": "S", "uuid": "u1"}]),
        encoding="utf-8",
    )
    (out_dir / "datasets" / "en" / "domains.json").write_text(
        json.dumps([{"slug": "orphan-dom", "name": "Orphan Domain Name"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "OUTPUT_ROOT", out_dir)
    client = TestClient(app)
    client.headers.update({"Origin": "http://testserver"})
    auth.init_db(); init_app_db()
    try: auth.create_user("testadmin2", "pw2", ["orphan-dom"], ["en"])
    except Exception: pass
    client.post("/login", data={"username": "testadmin2", "password": "pw2"})
    res = client.get("/api/v1/domains/orphan-dom?langs=en")
    assert res.status_code == 200
    data = res.json()
    assert data["slug"] == "orphan-dom"
    assert data["name"] != "orphan-dom"  # display_name pulled from syllabus
