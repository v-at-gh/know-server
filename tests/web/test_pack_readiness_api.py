"""API endpoint /api/v1/packs/{id}/readiness tests."""

import pytest


@pytest.fixture
def client(admin_client):
    return admin_client


def test_readiness_endpoint_returns_data(client):
    client.post("/api/v1/packs", json={
        "id": "mini", "kind": "custom",
        "domains": ["alpha"], "languages": ["en"],
        "selection": {
            "alpha": {"articles": ["art-1"], "questions_per_article": 5},
        },
    })
    r = client.get("/api/v1/packs/mini/readiness")
    assert r.status_code == 200
    data = r.json()
    assert data["pack_id"] == "mini"
    assert data["ready"] is True
    assert data["has_selection"] is True
    assert "alpha" in data["per_domain"]


def test_readiness_endpoint_404(client):
    r = client.get("/api/v1/packs/missing/readiness")
    assert r.status_code == 404
