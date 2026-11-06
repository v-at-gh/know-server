import sqlite3
import argparse
from scripts.reindex_vec import _reindex_batch

class FakeEmbedResult:
    def __init__(self, embeddings):
        self.embeddings = embeddings
        self.error = None

class FakeClient:
    def embed_batch(self, texts):
        return FakeEmbedResult([[0.1] * 128 for _ in texts])

def test_reindex_pending_staging(tmp_path):
    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, uuid TEXT, domain_id INTEGER, slug TEXT)")
    conn.execute("CREATE TABLE article_translations (article_id INTEGER, lang TEXT, title TEXT, summary TEXT, content TEXT)")
    conn.execute("CREATE TABLE article_embeddings_pending (uuid TEXT NOT NULL, lang TEXT NOT NULL, embedding BLOB NOT NULL, PRIMARY KEY (uuid, lang))")
    conn.execute("INSERT INTO articles (id, uuid, domain_id, slug) VALUES (1, 'u1', 1, 's1')")
    conn.execute("INSERT INTO article_translations (article_id, lang, title, summary, content) VALUES (1, 'en', 't', 's', 'c')")
    conn.commit()
    conn.close()

    client = FakeClient()
    parser = argparse.ArgumentParser()
    args = parser.parse_args([])
    args.model = "test-model"

    items = [("u1", "en")]
    errors, processed = _reindex_batch(db_path, items, True, client, args, lambda *a: None, type('Counter', (), {'add': lambda *a: None})(), pending=True)

    assert processed == 1
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT uuid, lang FROM article_embeddings_pending").fetchone()
    conn.close()
    assert row == ("u1", "en")
