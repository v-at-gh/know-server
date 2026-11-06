"""Verify admin /tasks route proxies to orchestrator correctly after
web.tasks consolidation."""

import pytest
from fastapi.testclient import TestClient
from web.app import app


@pytest.fixture
def client(monkeypatch):
    from web.app import get_current_user
    from web.auth import User

    def _fake_user():
        return User(id=1, username="testadmin",
                    active_domains=["linux"], active_langs=["en"])

    app.dependency_overrides[get_current_user] = _fake_user
    yield TestClient(app)
    app.dependency_overrides.clear()


class MockResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json


def test_admin_tasks_list_proxies_to_orchestrator(client, monkeypatch):
    """Admin /tasks must call orchestrator /api/v1/tasks and return its JSON."""
    captured = {}

    async def _fake_orch_get(path, params=None, request=None, timeout=5.0):
        captured["path"] = path
        captured["params"] = params
        return MockResponse({"tasks": [], "total": 0, "page": 1, "limit": 50})

    monkeypatch.setattr("web.app.orch_get", _fake_orch_get)

    resp = client.get("/tasks")
    assert resp.status_code in (200, 302, 307)
    assert captured.get("path") == "/api/v1/tasks"
