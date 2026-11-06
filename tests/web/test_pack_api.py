"""HTTP endpoint tests for /api/v1/packs/*."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(admin_client):
    return admin_client


def test_list_empty(client):
    r = client.get("/api/v1/packs")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 0
    assert data["packs"] == []


def test_create_and_list(client):
    r = client.post("/api/v1/packs", json={
        "id": "test-p", "kind": "campaign",
        "domains": ["linux"], "languages": ["en"],
    })
    assert r.status_code == 201
    r = client.get("/api/v1/packs")
    data = r.json()
    assert data["total"] == 1
    assert data["packs"][0]["definition"]["id"] == "test-p"
    assert data["packs"][0]["built"] is None


def test_create_duplicate_400(client):
    client.post("/api/v1/packs", json={
        "id": "dup", "kind": "campaign", "domains": [], "languages": []
    })
    r = client.post("/api/v1/packs", json={
        "id": "dup", "kind": "campaign", "domains": [], "languages": []
    })
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]


def test_patch_replaces_field(client):
    client.post("/api/v1/packs", json={
        "id": "patch-me", "kind": "campaign",
        "domains": ["a"], "languages": ["en"],
    })
    r = client.patch("/api/v1/packs/patch-me", json={"domains": ["b", "c"]})
    assert r.status_code == 200
    r = client.get("/api/v1/packs/patch-me")
    assert r.json()["definition"]["domains"] == ["b", "c"]


def test_delete_default_removes_definition(client, tmp_path):
    client.post("/api/v1/packs", json={
        "id": "del-me", "kind": "campaign", "domains": [], "languages": []
    })
    r = client.delete("/api/v1/packs/del-me")
    assert r.status_code == 200
    assert r.json()["definition_removed"] is True
    r = client.get("/api/v1/packs/del-me")
    assert r.status_code == 404


def test_delete_keep_artifact(client, tmp_path, monkeypatch):
    import quizkb.config as config
    qaps_dir = tmp_path / "qaps"
    qaps_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "QAPS_DIR", qaps_dir)
    artifact = qaps_dir / "keep-me.qap"
    artifact.write_bytes(b"fake")
    client.post("/api/v1/packs", json={
        "id": "keep-me", "kind": "campaign", "domains": [], "languages": []
    })
    r = client.delete("/api/v1/packs/keep-me?delete_artifact=false")
    assert r.status_code == 200
    assert r.json()["artifact_removed"] is False
    assert artifact.exists()  # still there


def test_unauthenticated_401(client):
    no_auth = TestClient(client.app, follow_redirects=False)
    r = no_auth.get("/api/v1/packs")
    assert r.status_code in (401, 303)
