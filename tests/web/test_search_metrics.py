import pytest
import sqlite3
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from web.app import app
import web.app as web_app
import quizkb.observability as obs
from tests._metric_helpers import find_metric_value, find_metric_datapoints

from web.auth import User


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(obs, "get_meter", lambda name: provider.get_meter(name))

    test_meter = provider.get_meter("admin")
    monkeypatch.setattr(
        web_app, "search_requests_total",
        test_meter.create_counter("search_requests_total"),
    )

    app.state.semantic_available = False

    def _fake_user():
        return User(
            id=1, username="testadmin",
            active_domains=["linux"], active_langs=["en"],
        )

    from web.app import get_current_user_or_token
    app.dependency_overrides[get_current_user_or_token] = _fake_user
    yield TestClient(app), reader
    app.dependency_overrides.clear()


def test_search_success_increments_counter(client, monkeypatch):
    test_client, reader = client
    monkeypatch.setattr(web_app.kb_db, "search_articles_hybrid", lambda *a, **kw: [])
    monkeypatch.setattr(web_app.kb_db, "search_questions_hybrid", lambda *a, **kw: [])

    resp = test_client.get("/api/v1/search?q=docker")
    assert resp.status_code == 200

    assert find_metric_value(reader, "search_requests_total",
                             {"result": "ok", "fallback": "fts"}) == 1


def test_empty_query_no_counter(client):
    test_client, reader = client
    resp = test_client.get("/api/v1/search?q=")
    assert resp.status_code == 200

    assert find_metric_datapoints(reader, "search_requests_total") == []


def test_search_error_increments_and_propagates(client, monkeypatch):
    test_client, reader = client

    def _boom(*a, **kw):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(web_app.kb_db, "search_articles_hybrid", _boom)

    with pytest.raises(sqlite3.OperationalError):
        test_client.get("/api/v1/search?q=docker")

    assert find_metric_value(reader, "search_requests_total",
                             {"result": "error", "fallback": "fts"}) == 1
