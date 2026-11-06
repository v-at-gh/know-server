import sqlite3
import json
import subprocess
import sys
import io
from pathlib import Path
import pytest
import sqlite_vec
from quizkb import db as kb_db
from scripts.reindex_vec import main as reindex_main

class MockEmbedClient:
    def __init__(self, embed_model=None, **kwargs):
        self.embed_model = embed_model
    def embed_batch(self, inputs, **kwargs):
        class Rec:
            embeddings = [[0.1] * 128 for _ in inputs]
            error = None
        return Rec()

class MockUnreachableClient:
    def __init__(self, embed_model=None, **kwargs):
        self.embed_model = embed_model
    def embed_batch(self, inputs, **kwargs):
        class Rec:
            embeddings = []
            error = "connection refused"
        return Rec()

@pytest.fixture
def tmp_content_db(tmp_path):
    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    conn.commit()
    conn.close()
    return db_path

@pytest.fixture
def tmp_content_db_with_data(tmp_path):
    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO domains (slug, kind) VALUES ('linux', 'topic')")
    d1 = cursor.lastrowid
    cursor.execute("INSERT INTO domains (slug, kind) VALUES ('python', 'language')")
    d2 = cursor.lastrowid

    cursor.execute("INSERT INTO articles (uuid, domain_id, slug) VALUES ('art-lin-1', ?, 'overview')", (d1,))
    a1 = cursor.lastrowid
    cursor.execute("INSERT INTO article_translations (article_id, lang, title, summary, content) VALUES (?, 'en', 'Linux', 'Sum', 'Cont')", (a1,))

    cursor.execute("INSERT INTO articles (uuid, domain_id, slug) VALUES ('art-py-1', ?, 'basics')", (d2,))
    a2 = cursor.lastrowid
    cursor.execute("INSERT INTO article_translations (article_id, lang, title, summary, content) VALUES (?, 'en', 'Python', 'Sum', 'Cont')", (a2,))

    cursor.execute("INSERT INTO questions (uuid, domain_id, difficulty) VALUES ('q-lin-1', ?, 1)", (d1,))
    q1 = cursor.lastrowid
    cursor.execute("INSERT INTO question_translations (question_id, lang, text, answer) VALUES (?, 'en', 'Linux Q?', 'Ans')", (q1,))

    conn.commit()
    conn.close()
    return db_path

def test_reindex_vec_help():
    res = subprocess.run([sys.executable, "scripts/reindex_vec.py", "--help"], capture_output=True, text=True)
    assert res.returncode == 0
    assert "--full" in res.stdout
    assert "--missing" in res.stdout
    assert "--batch-size" not in res.stdout

def test_reindex_vec_dry_run_missing(tmp_content_db, monkeypatch):
    monkeypatch.setattr("scripts.reindex_vec.DB_PATH", tmp_content_db)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing", "--dry-run"])

    captured = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', captured)

    with pytest.raises(SystemExit) as exc:
        reindex_main()
    assert exc.value.code == 0
    out = captured.getvalue()
    assert "Dry run: reindex_vec --missing" in out
    assert "0/0 missing" in out

    conn = sqlite3.connect(tmp_content_db)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    meta = conn.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0]
    art = conn.execute("SELECT COUNT(*) FROM article_embeddings").fetchone()[0]
    conn.close()
    assert meta == 0
    assert art == 0

def test_reindex_vec_missing_populates(tmp_content_db_with_data, monkeypatch):
    monkeypatch.setattr("scripts.reindex_vec.DB_PATH", tmp_content_db_with_data)
    monkeypatch.setattr("scripts.reindex_vec.OllamaClient", MockEmbedClient)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing"])

    with pytest.raises(SystemExit) as exc:
        reindex_main()
    assert exc.value.code == 0

    conn = sqlite3.connect(tmp_content_db_with_data)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    
    art_cnt = conn.execute("SELECT COUNT(*) FROM article_embeddings").fetchone()[0]
    q_cnt = conn.execute("SELECT COUNT(*) FROM question_embeddings").fetchone()[0]
    meta_cnt = conn.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0]
    conn.close()

    assert art_cnt == 2
    assert q_cnt == 1
    assert meta_cnt >= 2

def test_reindex_vec_missing_empty_populates_schema_meta(tmp_content_db, monkeypatch):
    monkeypatch.setattr("scripts.reindex_vec.DB_PATH", tmp_content_db)
    monkeypatch.setattr("scripts.reindex_vec.OllamaClient", MockEmbedClient)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing"])

    with pytest.raises(SystemExit) as exc:
        reindex_main()
    assert exc.value.code == 0

    conn = sqlite3.connect(tmp_content_db)
    meta = conn.execute("SELECT key, value FROM schema_meta ORDER BY key").fetchall()
    conn.close()
    assert len(meta) == 2
    keys = {k for k, _ in meta}
    assert keys == {"embed_model", "embed_dimensions"}

def test_reindex_vec_custom_model_uses_override(tmp_content_db_with_data, monkeypatch):
    used_models = []

    class MockTrackingClient:
        def __init__(self, embed_model=None, **kwargs):
            self.embed_model = embed_model
        def embed_batch(self, inputs, model=None, **kwargs):
            m = model or self.embed_model
            used_models.append(m)
            class Rec:
                embeddings = [[0.1] * 128 for _ in inputs]
                error = None
            return Rec()

    monkeypatch.setattr("scripts.reindex_vec.DB_PATH", tmp_content_db_with_data)
    monkeypatch.setattr("scripts.reindex_vec.OllamaClient", MockTrackingClient)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing", "--model", "custom-model"])

    with pytest.raises(SystemExit) as exc:
        reindex_main()
    assert exc.value.code == 0
    assert len(used_models) > 0
    assert all(m == "custom-model" for m in used_models)

def test_reindex_vec_full_rebuilds(tmp_content_db_with_data, monkeypatch):
    conn = sqlite3.connect(tmp_content_db_with_data)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO article_embeddings (embedding) VALUES (?)", (sqlite3.Binary(sqlite_vec.serialize_float32([0.1]*128)),))
    vrow = cursor.lastrowid
    cursor.execute("INSERT INTO article_embedding_index (uuid, lang, vec_rowid) VALUES ('stale-uuid', 'en', ?)", (vrow,))
    conn.commit()
    conn.close()

    monkeypatch.setattr("scripts.reindex_vec.DB_PATH", tmp_content_db_with_data)
    monkeypatch.setattr("scripts.reindex_vec.OllamaClient", MockEmbedClient)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--full"])

    with pytest.raises(SystemExit) as exc:
        reindex_main()
    assert exc.value.code == 0

    conn = sqlite3.connect(tmp_content_db_with_data)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    
    stale = conn.execute("SELECT vec_rowid FROM article_embedding_index WHERE uuid = 'stale-uuid'").fetchone()
    art_cnt = conn.execute("SELECT COUNT(*) FROM article_embeddings").fetchone()[0]
    conn.close()

    assert stale is None
    assert art_cnt == 2

def test_reindex_vec_full_rebuilds_existing_and_missing_embeddings(tmp_content_db_with_data, monkeypatch):
    conn = sqlite3.connect(tmp_content_db_with_data)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO article_embeddings (embedding) VALUES (?)",
        (sqlite3.Binary(sqlite_vec.serialize_float32([0.2] * 128)),),
    )
    vrow = cursor.lastrowid
    cursor.execute(
        "INSERT INTO article_embedding_index (uuid, lang, vec_rowid) VALUES ('art-lin-1', 'en', ?)",
        (vrow,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("scripts.reindex_vec.DB_PATH", tmp_content_db_with_data)
    monkeypatch.setattr("scripts.reindex_vec.OllamaClient", MockEmbedClient)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--full"])

    with pytest.raises(SystemExit) as exc:
        reindex_main()
    assert exc.value.code == 0

    conn = sqlite3.connect(tmp_content_db_with_data)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    indexed_articles = {
        row[0]
        for row in conn.execute("SELECT uuid FROM article_embedding_index")
    }
    art_cnt = conn.execute("SELECT COUNT(*) FROM article_embeddings").fetchone()[0]
    conn.close()

    assert indexed_articles == {"art-lin-1", "art-py-1"}
    assert art_cnt == 2

def test_reindex_vec_prune(tmp_content_db, monkeypatch):
    conn = sqlite3.connect(tmp_content_db)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    cursor = conn.cursor()
    for i in range(3):
        cursor.execute("INSERT INTO article_embeddings (embedding) VALUES (?)", (sqlite3.Binary(sqlite_vec.serialize_float32([0.1]*128)),))
        vrow = cursor.lastrowid
        cursor.execute("INSERT INTO article_embedding_index (uuid, lang, vec_rowid) VALUES (?, 'en', ?)", (f"orphan-{i}", vrow))
    conn.commit()
    conn.close()

    monkeypatch.setattr("scripts.reindex_vec.DB_PATH", tmp_content_db)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--prune"])

    with pytest.raises(SystemExit) as exc:
        reindex_main()
    assert exc.value.code == 0

    conn = sqlite3.connect(tmp_content_db)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    cnt = conn.execute("SELECT COUNT(*) FROM article_embeddings").fetchone()[0]
    conn.close()
    assert cnt == 0

def test_reindex_vec_full_ollama_unreachable(tmp_content_db_with_data, monkeypatch):
    monkeypatch.setattr("scripts.reindex_vec.DB_PATH", tmp_content_db_with_data)
    monkeypatch.setattr("scripts.reindex_vec.OllamaClient", MockUnreachableClient)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--full"])

    with pytest.raises(SystemExit) as exc:
        reindex_main()
    assert exc.value.code == 1

    conn = sqlite3.connect(tmp_content_db_with_data)
    meta = conn.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0]
    conn.close()
    assert meta == 0

def test_reindex_vec_domain_targeted(tmp_content_db_with_data, monkeypatch):
    monkeypatch.setattr("scripts.reindex_vec.DB_PATH", tmp_content_db_with_data)
    monkeypatch.setattr("scripts.reindex_vec.OllamaClient", MockEmbedClient)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--domain", "linux"])

    with pytest.raises(SystemExit) as exc:
        reindex_main()
    assert exc.value.code == 0

    conn = sqlite3.connect(tmp_content_db_with_data)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    
    lin_art = conn.execute("SELECT vec_rowid FROM article_embedding_index WHERE uuid = 'art-lin-1'").fetchone()
    py_art = conn.execute("SELECT vec_rowid FROM article_embedding_index WHERE uuid = 'art-py-1'").fetchone()
    conn.close()

    assert lin_art is not None
    assert py_art is None

def test_reindex_vec_missing_on_fresh_db_with_content(tmp_content_db_with_data, monkeypatch):
    conn = sqlite3.connect(tmp_content_db_with_data)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("DROP TABLE IF EXISTS article_embeddings")
    conn.execute("DROP TABLE IF EXISTS article_embedding_index")
    conn.execute("DROP TABLE IF EXISTS question_embeddings")
    conn.execute("DROP TABLE IF EXISTS question_embedding_index")
    conn.execute("DROP TABLE IF EXISTS schema_meta")
    conn.commit()
    conn.close()

    monkeypatch.setattr("scripts.reindex_vec.DB_PATH", tmp_content_db_with_data)
    monkeypatch.setattr("scripts.reindex_vec.OllamaClient", MockEmbedClient)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing"])

    with pytest.raises(SystemExit) as exc:
        reindex_main()
    assert exc.value.code == 0

    conn = sqlite3.connect(tmp_content_db_with_data)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    art_cnt = conn.execute("SELECT COUNT(*) FROM article_embeddings").fetchone()[0]
    meta_cnt = conn.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0]
    conn.close()

    assert art_cnt == 2
    assert meta_cnt >= 2

def test_dry_run_missing_on_pre_semantic_db(tmp_content_db_with_data, monkeypatch):
    conn = sqlite3.connect(tmp_content_db_with_data)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    for t in ["article_embeddings", "article_embedding_index",
              "question_embeddings", "question_embedding_index",
              "schema_meta"]:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
    conn.close()

    monkeypatch.setattr("scripts.reindex_vec.DB_PATH", tmp_content_db_with_data)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing", "--dry-run"])

    captured = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', captured)

    with pytest.raises(SystemExit) as exc:
        reindex_main()
    assert exc.value.code == 0
    out = captured.getvalue()
    assert "2/2 missing" in out
    assert "1/1 missing" in out

    conn = sqlite3.connect(tmp_content_db_with_data)
    tbls = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "article_embeddings" not in tbls
    assert "article_embedding_index" not in tbls
    assert "question_embeddings" not in tbls
    assert "schema_meta" not in tbls
