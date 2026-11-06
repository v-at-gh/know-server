"""Integration/parity tests for search view and API search in web/app.py."""
import pytest


def test_search_view_returns_semantic_flag(admin_client, monkeypatch):
    """Jinja /search renders vector search indicator when semantic_used is True."""
    from web import app as web_app
    def fake_run(*args, **kwargs):
        return [], [], True, 5, "en"
    monkeypatch.setattr(web_app, "run_search", fake_run)
    r = admin_client.get("/search?q=test")
    assert r.status_code == 200
    assert "Vector Search Enabled" in r.text


def test_api_search_returns_semantic_flag(admin_client, monkeypatch):
    """REST /api/v1/search response includes semantic_available flag."""
    from web import app as web_app
    def fake_run(*args, **kwargs):
        return [], [], True, 5, "en"
    monkeypatch.setattr(web_app, "run_search", fake_run)
    r = admin_client.get("/api/v1/search?q=test")
    assert r.status_code == 200
    data = r.json()
    assert data["semantic_available"] is True
    assert data["elapsed_ms"] == 5


def test_api_search_returns_none_lang_when_invalid(admin_client, monkeypatch):
    """If requested lang not in active_langs, response.lang is None (honest contract)."""
    from web import app as web_app
    def fake_run(db, q, lang, t, **kw):
        effective = lang if lang in kw.get("active_langs", []) else None
        return [], [], False, 5, effective
    monkeypatch.setattr(web_app, "run_search", fake_run)
    r = admin_client.get("/api/v1/search?q=test&lang=xx")  # xx not in active
    assert r.status_code == 200
    data = r.json()
    assert data["lang"] is None  # dropped, not "xx"


def test_api_search_returns_requested_lang_when_valid(admin_client, monkeypatch):
    """If requested lang is active, response.lang reflects it."""
    from web import app as web_app
    def fake_run(db, q, lang, t, **kw):
        effective = lang if lang in kw.get("active_langs", []) else None
        return [], [], False, 5, effective
    monkeypatch.setattr(web_app, "run_search", fake_run)
    r = admin_client.get("/api/v1/search?q=test&lang=en")  # en is active
    assert r.status_code == 200
    data = r.json()
    assert data["lang"] == "en"
