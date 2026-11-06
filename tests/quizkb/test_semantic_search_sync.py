import sqlite3
import json
from pathlib import Path
import pytest
import sqlite_vec
from quizkb import db as kb_db
from quizkb.client import OllamaClient, OLLAMA_EMBED_MODEL, EMBED_DIMENSIONS
from fastapi.testclient import TestClient
from web.app import app
from web import auth
from web.db import init_app_db

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
            error = "Ollama embedding failed"
        return Rec()

@pytest.fixture
def client():
    c = TestClient(app)
    c.headers.update({"Origin": "http://testserver"})
    auth.init_db()
    init_app_db()
    try:
        auth.create_user("testadmin", "password123", ["linux", "python"], ["en", "ru"])
    except Exception:
        pass
    c.post("/login", data={"username": "testadmin", "password": "password123"})
    return c

def test_reindex_article_creates_embedding(tmp_path):
    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO domains (slug, kind) VALUES ('history', 'topic')")
    did = cursor.lastrowid
    cursor.execute("INSERT INTO articles (uuid, domain_id, slug) VALUES ('art-uuid-1', ?, 'rome')", (did,))
    aid = cursor.lastrowid
    cursor.execute("INSERT INTO article_translations (article_id, lang, title, summary, content) VALUES (?, 'en', 'Rome', 'Summary', 'Content')", (aid,))
    conn.commit()
    conn.close()

    client_emb = MockEmbedClient()
    success, _ = kb_db.reindex_article(db_path, "art-uuid-1", "en", client_emb)
    assert success is True

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    cnt = conn.execute("SELECT COUNT(*) FROM article_embeddings").fetchone()[0]
    assert cnt == 1
    conn.close()

def test_reindex_article_updates_embedding(tmp_path):
    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO domains (slug, kind) VALUES ('history', 'topic')")
    did = cursor.lastrowid
    cursor.execute("INSERT INTO articles (uuid, domain_id, slug) VALUES ('art-uuid-1', ?, 'rome')", (did,))
    aid = cursor.lastrowid
    cursor.execute("INSERT INTO article_translations (article_id, lang, title, summary, content) VALUES (?, 'en', 'Rome', 'Summary', 'Content')", (aid,))
    conn.commit()
    conn.close()

    client_emb = MockEmbedClient()
    kb_db.reindex_article(db_path, "art-uuid-1", "en", client_emb)
    success, _ = kb_db.reindex_article(db_path, "art-uuid-1", "en", client_emb)
    assert success is True

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    cnt = conn.execute("SELECT COUNT(*) FROM article_embeddings").fetchone()[0]
    assert cnt == 1
    conn.close()

def test_reindex_article_missing_translation_returns_false(tmp_path):
    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    conn.commit()
    conn.close()

    client_emb = MockEmbedClient()
    success, err = kb_db.reindex_article(db_path, "nonexistent", "en", client_emb)
    assert success is False
    assert err == "no_translation"

def test_reindex_article_embed_error_returns_false_no_corruption(tmp_path):
    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO domains (slug, kind) VALUES ('history', 'topic')")
    did = cursor.lastrowid
    cursor.execute("INSERT INTO articles (uuid, domain_id, slug) VALUES ('art-uuid-1', ?, 'rome')", (did,))
    aid = cursor.lastrowid
    cursor.execute("INSERT INTO article_translations (article_id, lang, title, summary, content) VALUES (?, 'en', 'Rome', 'Summary', 'Content')", (aid,))
    conn.commit()
    conn.close()

    client_emb = MockFailEmbedClient()
    success, err = kb_db.reindex_article(db_path, "art-uuid-1", "en", client_emb)
    assert success is False

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    cnt = conn.execute("SELECT COUNT(*) FROM article_embeddings").fetchone()[0]
    assert cnt == 0
    conn.close()

def test_sync_all_reembed_false_no_embeddings(tmp_path):
    db_path = tmp_path / "content.db"
    output_root = tmp_path / "output"
    packs_json = tmp_path / "packs.json"
    packs_json.write_text('{"packs":[]}', encoding="utf-8")

    kb_db.sync_all(db_path, output_root, packs_json, reembed=False)

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    cnt = conn.execute("SELECT COUNT(*) FROM article_embeddings").fetchone()[0]
    assert cnt == 0
    conn.close()

def test_prune_orphans_removes_vec_and_index(tmp_path):
    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO article_embeddings (embedding) VALUES (?)", (sqlite3.Binary(sqlite_vec.serialize_float32([0.1]*128)),))
    vrow = cursor.lastrowid
    cursor.execute("INSERT INTO article_embedding_index (uuid, lang, vec_rowid) VALUES ('orphan-uuid', 'en', ?)", (vrow,))
    conn.commit()
    conn.close()

    stats = kb_db.prune_orphan_embeddings(db_path)
    assert stats["articles_deleted"] > 0

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    cnt = conn.execute("SELECT COUNT(*) FROM article_embeddings").fetchone()[0]
    assert cnt == 0
    conn.close()

def test_edit_save_does_not_reindex_on_validation_400(client, monkeypatch):
    app.state.semantic_available = True
    app.state.ollama_client = MockEmbedClient()

    response = client.post(
        "/question/test-uuid-400/edit",
        params={"lang": "en", "domain": "linux"},
        data={
            "text": "What is Linux?",
            "type": "select",
            "answer": "OS",
            "answerWhy": "Because",
            "distractor_text": ["OS", "Browser"],
            "distractor_why": ["Not why", "Not why"],
            "hints_text": "Hint 1",
            "difficulty": "1"
        },
        follow_redirects=False
    )
    assert response.status_code == 400
