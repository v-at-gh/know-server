"""Display and TTL cache tests for pack readiness."""

import time
import json
import pytest


@pytest.fixture
def client(admin_client):
    return admin_client


def test_packs_list_renders_selection_column(client):
    client.post("/api/v1/packs", json={
        "id": "sel-pack", "kind": "custom",
        "domains": ["alpha"], "languages": ["en"],
        "selection": {"alpha": {"articles": ["art-1"], "questions_per_article": 5}},
    })
    r = client.get("/packs")
    assert r.status_code == 200
    assert "1 dom / 1 art" in r.text


def test_pack_detail_renders_readiness_card(client, monkeypatch):
    client.post("/api/v1/packs", json={
        "id": "detail-pack", "kind": "custom",
        "domains": ["alpha"], "languages": ["en"],
        "selection": {"alpha": {"articles": ["art-1"], "questions_per_article": 5}},
    })
    from quizkb.config import QAPS_DIR, QAPS_INDEX
    QAPS_DIR.mkdir(parents=True, exist_ok=True)
    qap_path = QAPS_DIR / "detail-pack.qap"
    qap_path.write_bytes(b"fake")

    manifest = QAPS_INDEX.parent / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "packs": [{
            "id": "detail-pack", "file": "detail-pack.qap", "version": "1.0",
            "size": 100, "sha256": "abc", "kind": "custom", "createdAt": "2026",
            "languages": ["en"], "campaigns": [], "domains": ["alpha"], "totals": {}
        }]
    }), encoding="utf-8")

    from quizkb import packs as kb_packs
    monkeypatch.setattr(kb_packs, "load_detail", lambda entry: kb_packs.PackDetail(
        entry=entry, domains=[], campaigns=[], files=[], articles_total=1, questions_total=5
    ))

    r = client.get("/pack/detail-pack")
    assert r.status_code == 200
    assert "Selection Readiness" in r.text
    assert "ready ✓" in r.text

    from pathlib import Path
    assert not (Path("state") / "qaps" / "detail-pack.qap").exists()


def test_readiness_api_cached_within_ttl(client, monkeypatch):
    client.post("/api/v1/packs", json={
        "id": "cache-pack", "kind": "custom",
        "domains": ["alpha"], "languages": ["en"],
        "selection": {"alpha": {"articles": ["art-1"], "questions_per_article": 5}},
    })

    import quizkb.packs as kb_packs
    orig = kb_packs.compute_pack_readiness
    calls = 0

    def mock_compute(pack_def, output_root):
        nonlocal calls
        calls += 1
        return orig(pack_def, output_root)

    monkeypatch.setattr(kb_packs, "compute_pack_readiness", mock_compute)

    r1 = client.get("/api/v1/packs/cache-pack/readiness")
    assert r1.status_code == 200
    r2 = client.get("/api/v1/packs/cache-pack/readiness")
    assert r2.status_code == 200
    assert calls == 1


def test_readiness_cache_invalidated_on_update(client, monkeypatch):
    client.post("/api/v1/packs", json={
        "id": "inv-pack", "kind": "custom",
        "domains": ["alpha"], "languages": ["en"],
        "selection": {"alpha": {"articles": ["art-1"], "questions_per_article": 5}},
    })

    import quizkb.packs as kb_packs
    orig = kb_packs.compute_pack_readiness
    calls = 0

    def mock_compute(pack_def, output_root):
        nonlocal calls
        calls += 1
        return orig(pack_def, output_root)

    monkeypatch.setattr(kb_packs, "compute_pack_readiness", mock_compute)

    client.get("/api/v1/packs/inv-pack/readiness")
    assert calls == 1

    client.patch("/api/v1/packs/inv-pack", json={"languages": ["en"]})

    client.get("/api/v1/packs/inv-pack/readiness")
    assert calls == 2


def test_readiness_cache_ttl_expires(client, monkeypatch):
    client.post("/api/v1/packs", json={
        "id": "ttl-pack", "kind": "custom",
        "domains": ["alpha"], "languages": ["en"],
        "selection": {"alpha": {"articles": ["art-1"], "questions_per_article": 5}},
    })

    import quizkb.packs as kb_packs
    import web.app as web_app
    orig = kb_packs.compute_pack_readiness
    calls = 0

    def mock_compute(pack_def, output_root):
        nonlocal calls
        calls += 1
        return orig(pack_def, output_root)

    monkeypatch.setattr(kb_packs, "compute_pack_readiness", mock_compute)

    client.get("/api/v1/packs/ttl-pack/readiness")
    assert calls == 1

    current_time = time.time()
    monkeypatch.setattr(web_app._time, "time", lambda: current_time + 31.0)

    client.get("/api/v1/packs/ttl-pack/readiness")
    assert calls == 2
