import sqlite3
import pytest
import sqlite_vec
from unittest.mock import MagicMock
from quizkb import db as kb_db
from quizkb.client import EmbedRecord
from scripts.reindex_vec import _reindex_batch


def test_reindex_batches_embeddings(tmp_path, monkeypatch):
    """Verify embed_batch called with >1 item on multi-item reindex."""
    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO domains (slug, kind) VALUES ('linux', 'topic')")
    d1 = cursor.lastrowid
    
    # Insert multiple articles
    for i in range(10):
        uuid = f"art-{i}"
        cursor.execute("INSERT INTO articles (uuid, domain_id, slug) VALUES (?, ?, ?)", (uuid, d1, f"slug-{i}"))
        aid = cursor.lastrowid
        cursor.execute("INSERT INTO article_translations (article_id, lang, title, summary, content) VALUES (?, 'en', ?, 'Sum', 'Cont')", (aid, f"Title {i}"))
    conn.commit()
    conn.close()

    call_sizes = []
    class MockBatchClient:
        def embed_batch(self, inputs, **kwargs):
            call_sizes.append(len(inputs))
            return EmbedRecord(
                embeddings=[[0.1]*128 for _ in inputs],
                prompt_tokens=0,
                latency_ms=0,
                created_at="2026-11-02T00:00:00Z"
            )

    client = MockBatchClient()
    items = [(f"art-{i}", "en") for i in range(10)]
    
    class Args:
        model = "test-model"

    mock_errors = MagicMock()
    errors, processed = _reindex_batch(db_path, items, is_article=True, client=client, args=Args(), progress_ctx=lambda p, u: None, ollama_errors=mock_errors)
    assert errors == 0
    assert processed == 10
    assert any(size > 1 for size in call_sizes)


def test_reindex_progress_increments(tmp_path):
    """Verify progress_ctx called with increasing processed values, not 0."""
    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO domains (slug, kind) VALUES ('linux', 'topic')")
    d1 = cursor.lastrowid
    
    for i in range(10):
        uuid = f"art-{i}"
        cursor.execute("INSERT INTO articles (uuid, domain_id, slug) VALUES (?, ?, ?)", (uuid, d1, f"slug-{i}"))
        aid = cursor.lastrowid
        cursor.execute("INSERT INTO article_translations (article_id, lang, title, summary, content) VALUES (?, 'en', ?, 'Sum', 'Cont')", (aid, f"Title {i}"))
    conn.commit()
    conn.close()

    progress_calls = []
    class MockBatchClient:
        def embed_batch(self, inputs, **kwargs):
            return EmbedRecord(
                embeddings=[[0.1]*128 for _ in inputs],
                prompt_tokens=0,
                latency_ms=0,
                created_at="2026-11-02T00:00:00Z"
            )

    client = MockBatchClient()
    items = [(f"art-{i}", "en") for i in range(10)]
    class Args:
        model = "test-model"

    mock_errors = MagicMock()
    errors, processed = _reindex_batch(db_path, items, is_article=True, client=client, args=Args(), progress_ctx=lambda p, u: progress_calls.append(p), ollama_errors=mock_errors)
    assert errors == 0
    assert processed == 10
    assert len(progress_calls) >= 10
    assert progress_calls[-1] == 10
    assert progress_calls[0] >= 1
