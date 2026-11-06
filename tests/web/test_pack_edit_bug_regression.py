"""Regression: edit must reflect in /packs display immediately, without rebuild."""

import json
import pytest


@pytest.fixture
def client(admin_client, tmp_path, monkeypatch):
    import quizkb.config as config
    packs_json = config.PACKS_JSON
    qaps_dir = tmp_path / "qaps"
    manifest_json = tmp_path / "manifest.json"
    qaps_dir.mkdir()

    monkeypatch.setattr(config, "QAPS_DIR", qaps_dir)
    monkeypatch.setattr(config, "QAPS_INDEX", manifest_json)
    monkeypatch.setattr("quizkb.packs.QAPS_DIR", qaps_dir, raising=False)
    monkeypatch.setattr("quizkb.packs.QAPS_INDEX", manifest_json, raising=False)

    return admin_client, tmp_path, packs_json, qaps_dir, manifest_json


def test_edit_reflects_immediately_without_rebuild(client):
    """Original bug: user edits pack via UI, but /packs list shows stale data
    from .qap manifest. Fix: spec is source of truth, .qap informational.
    """
    c, tmp_path, packs_json, qaps_dir, manifest_json = client

    # 1. Create pack definition
    c.post("/api/v1/packs", json={
        "id": "editbug", "kind": "campaign",
        "domains": ["a", "b"], "languages": ["en"],
    })

    # 2. Simulate "built" state — fake manifest with OLD domains ["a", "b"]
    manifest_json.write_text(json.dumps({
        "generatedAt": "2026-01-01",
        "packs": [{
            "id": "editbug", "version": "2026.01.01",
            "size": 1000, "sha256": "abc",
            "domains": ["a", "b"], "languages": ["en"], "campaigns": [],
        }],
    }), encoding="utf-8")
    (qaps_dir / "editbug.qap").write_bytes(b"fake")

    # 3. Edit pack: remove domain "b"
    r = c.patch("/api/v1/packs/editbug", json={"domains": ["a"]})
    assert r.status_code == 200

    # 4. Fetch listing — definition must reflect edit immediately
    r = c.get("/api/v1/packs/editbug")
    data = r.json()
    assert data["definition"]["domains"] == ["a"]
    assert data["built"]["domains"] == ["a", "b"]  # built is stale
    assert data["built_is_stale"] is True  # flag surfaced
