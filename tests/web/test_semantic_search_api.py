import sqlite3
import json
from pathlib import Path
import pytest
import sqlite_vec
from quizkb import db as kb_db
from quizkb.client import OllamaClient
from fastapi.testclient import TestClient
from web.app import app
from web import auth

class MockEmbedClient:
    def embed_batch(self, inputs, **kwargs):
        class Rec:
            embeddings = [[0.1] * 128 for _ in inputs]
            error = None
        return Rec()

class MockFailEmbedClient:
    def embed_batch(self, inputs, **kwargs):
        class Rec:
            embeddings = []
            error = "Ollama query embed failed"
        return Rec()

@pytest.fixture
def client_with_content(tmp_path, monkeypatch):
    db_path = tmp_path / "content.db"
    monkeypatch.setattr("quizkb.users.USERS_DB_PATH", tmp_path / "users.db")
    output_root = tmp_path / "output"
    packs_json = tmp_path / "packs.json"

    en_dir = output_root / "datasets" / "en"
    ru_dir = output_root / "datasets" / "ru"
    en_dir.mkdir(parents=True, exist_ok=True)
    ru_dir.mkdir(parents=True, exist_ok=True)

    domains_json = [{"slug": "linux", "name": "Linux OS"}]
    (en_dir / "domains.json").write_text(json.dumps(domains_json), encoding="utf-8")
    (ru_dir / "domains.json").write_text(json.dumps(domains_json), encoding="utf-8")

    en_kn = output_root / "knowledge" / "en" / "linux"
    ru_kn = output_root / "knowledge" / "ru" / "linux"
    en_kn.mkdir(parents=True, exist_ok=True)
    ru_kn.mkdir(parents=True, exist_ok=True)

    (en_kn / "overview.md").write_text("# Linux Overview\nLinux kernel and OS details.", encoding="utf-8")
    (ru_kn / "overview.md").write_text("# Обзор Linux\nДетали ядра и ОС Linux.", encoding="utf-8")

    (en_kn.parent / "index.json").write_text(json.dumps([
        {"uuid": "art-linux-overview", "domainSlug": "linux", "slug": "overview", "title": "Linux Overview", "description": "Linux OS summary"}
    ]), encoding="utf-8")
    (ru_kn.parent / "index.json").write_text(json.dumps([
        {"uuid": "art-linux-overview", "domainSlug": "linux", "slug": "overview", "title": "Обзор Linux", "description": "Резюме Linux"}
    ]), encoding="utf-8")

    q_en = [{
        "uuid": "q-linux-1",
        "text": "What is Linux?",
        "answer": "An operating system kernel",
        "distractors": [{"text": "Browser", "why": "No"}],
        "hints": ["Free software"],
        "difficulty": 1
    }]
    q_ru = [{
        "uuid": "q-linux-1",
        "text": "Что такое Linux?",
        "answer": "Операционная система",
        "distractors": [{"text": "Браузер", "why": "Нет"}],
        "hints": ["Свободное ПО"],
        "difficulty": 1
    }]
    (en_dir / "linux.json").write_text(json.dumps(q_en), encoding="utf-8")
    (ru_dir / "linux.json").write_text(json.dumps(q_ru), encoding="utf-8")
    packs_json.write_text('{"packs":[]}', encoding="utf-8")

    kb_db.sync_all(db_path, output_root, packs_json, reembed=False)

    monkeypatch.setattr("web.app.DB_PATH", db_path)
    monkeypatch.setattr("web.app.OUTPUT_ROOT", output_root)

    c = TestClient(app)
    c.headers.update({"Origin": "http://testserver"})
    auth.init_db()
    auth.create_user("testadmin", "password123", ["linux"], ["en"])
    with auth.get_db() as conn:
        conn.execute("DELETE FROM users WHERE username = ?", ("testadmin",))
        conn.commit()
    auth.create_user("testadmin", "password123", ["linux"], ["en", "ru"])
    c.post("/login", data={"username": "testadmin", "password": "password123"})
    return c

def test_search_api_fts_only_when_unavailable(client_with_content):
    app.state.semantic_available = False
    app.state.ollama_client = None

    resp = client_with_content.get("/api/v1/search?q=Linux")
    assert resp.status_code == 200
    data = resp.json()
    assert data["semantic_available"] is False
    assert "elapsed_ms" in data
    assert isinstance(data["articles"], list)
    assert isinstance(data["questions"], list)

def test_search_api_hybrid_returns_rrf_score(client_with_content, monkeypatch):
    app.state.semantic_available = True
    mock_client = MockEmbedClient()
    app.state.ollama_client = mock_client

    from web.app import DB_PATH
    kb_db.reindex_article(DB_PATH, "art-linux-overview", "en", mock_client)
    kb_db.reindex_question(DB_PATH, "q-linux-1", "en", mock_client)

    resp = client_with_content.get("/api/v1/search?q=Linux")
    assert resp.status_code == 200
    data = resp.json()
    assert data["semantic_available"] is True
    assert len(data["articles"]) > 0, "hybrid search returned no articles"
    assert data["articles"][0]["rrf_score"] > 0

def test_search_api_lang_filter(client_with_content):
    app.state.semantic_available = False
    resp = client_with_content.get("/api/v1/search?q=Linux&lang=ru")
    assert resp.status_code == 200
    data = resp.json()
    assert data["lang"] == "ru"
    assert len(data["articles"]) > 0
    for art in data["articles"]:
        assert art["lang"] == "ru"

def test_search_api_embed_error_degrades(client_with_content):
    app.state.semantic_available = True
    app.state.ollama_client = MockFailEmbedClient()

    resp = client_with_content.get("/api/v1/search?q=Linux")
    assert resp.status_code == 200
    data = resp.json()
    assert data["semantic_available"] is False
    assert "articles" in data

def test_search_api_elapsed_ms_present(client_with_content):
    resp = client_with_content.get("/api/v1/search?q=Linux")
    assert resp.status_code == 200
    data = resp.json()
    assert "elapsed_ms" in data
    assert data["elapsed_ms"] >= 0

def test_search_api_type_filter(client_with_content):
    resp = client_with_content.get("/api/v1/search?q=Linux&type=articles")
    assert resp.status_code == 200
    data = resp.json()
    assert data["questions"] == []

    resp_q = client_with_content.get("/api/v1/search?q=Linux&type=questions")
    assert resp_q.status_code == 200
    data_q = resp_q.json()
    assert data_q["articles"] == []

def test_search_api_fts_syntax_error_fallback(client_with_content):
    """Malformed FTS syntax must return 200 with empty results, not 500."""
    app.state.semantic_available = False
    resp = client_with_content.get('/api/v1/search?q="unbalanced')
    assert resp.status_code == 200
    data = resp.json()
    assert data["articles"] == []
    assert data["questions"] == []
