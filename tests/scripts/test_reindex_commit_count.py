import sqlite3
import argparse
from scripts.reindex_vec import _reindex_batch

class CommitTrackingConnection:
    def __init__(self, conn, commit_dict):
        self._conn = conn
        self._commit_dict = commit_dict

    def commit(self):
        self._commit_dict["n"] += 1
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

class FakeEmbedResult:
    def __init__(self, embeddings):
        self.embeddings = embeddings
        self.error = None

class FakeClient:
    def embed_batch(self, texts):
        return FakeEmbedResult([[0.1] * 128 for _ in texts])

def test_commit_count_per_batch(monkeypatch, tmp_path):
    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, uuid TEXT, domain_id INTEGER, slug TEXT)")
    conn.execute("CREATE TABLE article_translations (article_id INTEGER, lang TEXT, title TEXT, summary TEXT, content TEXT)")
    conn.execute("CREATE TABLE article_embedding_index (uuid TEXT, lang TEXT, vec_rowid INTEGER)")
    conn.execute("CREATE TABLE article_embeddings (embedding BLOB)")
    conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO articles (id, uuid, domain_id, slug) VALUES (1, 'u1', 1, 's1')")
    conn.execute("INSERT INTO article_translations (article_id, lang, title, summary, content) VALUES (1, 'en', 't', 's', 'c')")
    conn.commit()
    conn.close()

    commit_count = {"n": 0}
    real_connect = sqlite3.connect

    def tracking_connect(*a, **kw):
        conn = real_connect(*a, **kw)
        return CommitTrackingConnection(conn, commit_count)

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)

    client = FakeClient()
    parser = argparse.ArgumentParser()
    args = parser.parse_args([])
    args.model = "test-model"

    items = [("u1", "en")]
    errors, processed = _reindex_batch(db_path, items, True, client, args, lambda *a: None, type('Counter', (), {'add': lambda *a: None})())

    assert commit_count["n"] <= 2, f"Expected <= 2 commits, got {commit_count['n']}"
    assert processed == 1
