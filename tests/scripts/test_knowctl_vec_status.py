import sqlite3
import json
import sys
import io
import pytest
import sqlite_vec
from quizkb import db as kb_db
from scripts.knowctl import cmd_status

def test_knowctl_status_reports_vec_coverage(tmp_path, monkeypatch):
    db_path = tmp_path / "state" / "content.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)

    cur = conn.cursor()
    # Insert schema_meta
    cur.execute("INSERT INTO schema_meta (key, value) VALUES ('embed_model', 'embeddinggemma:latest')")
    cur.execute("INSERT INTO schema_meta (key, value) VALUES ('embed_dimensions', '128')")

    # Insert article and translation and vec
    cur.execute("INSERT INTO domains (slug, kind) VALUES ('linux', 'topic')")
    did = cur.lastrowid
    cur.execute("INSERT INTO articles (uuid, domain_id, slug) VALUES ('art-1', ?, 'overview')", (did,))
    aid = cur.lastrowid
    cur.execute("INSERT INTO article_translations (article_id, lang, title) VALUES (?, 'en', 'Linux')", (aid,))
    
    emb = [0.1] * 128
    cur.execute("INSERT INTO article_embeddings (embedding) VALUES (?)", (sqlite3.Binary(sqlite_vec.serialize_float32(emb)),))
    vrow = cur.lastrowid
    cur.execute("INSERT INTO article_embedding_index (uuid, lang, vec_rowid) VALUES ('art-1', 'en', ?)", (vrow,))

    # Insert question and translation and vec
    cur.execute("INSERT INTO questions (uuid, domain_id) VALUES ('q-1', ?)", (did,))
    qid = cur.lastrowid
    cur.execute("INSERT INTO question_translations (question_id, lang, text, answer) VALUES (?, 'en', 'Q?', 'A')", (qid,))
    cur.execute("INSERT INTO question_embeddings (embedding) VALUES (?)", (sqlite3.Binary(sqlite_vec.serialize_float32(emb)),))
    qvrow = cur.lastrowid
    cur.execute("INSERT INTO question_embedding_index (uuid, lang, vec_rowid) VALUES ('q-1', 'en', ?)", (qvrow,))

    conn.commit()
    conn.close()

    monkeypatch.setattr("scripts.knowctl.REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["knowctl.py", "status", "--json"])

    class Args:
        json = True
        verbose = 0

    captured = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', captured)

    try:
        cmd_status(Args())
    except SystemExit:
        pass

    out = captured.getvalue()
    data = json.loads(out)
    assert data["vec"]["initialized"] is True
    assert data["vec"]["articles_with_vec"] == 1
    assert data["vec"]["articles_total"] == 1
    assert data["vec"]["questions_with_vec"] == 1
    assert data["vec"]["questions_total"] == 1

def test_status_initialized_false_when_schema_meta_empty(tmp_path, monkeypatch):
    db_path = tmp_path / "state" / "content.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)  # creates schema_meta but leaves it empty
    conn.commit()
    conn.close()

    monkeypatch.setattr("scripts.knowctl.REPO_ROOT", tmp_path)

    class Args:
        json = True
        verbose = 0

    captured = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', captured)

    try:
        cmd_status(Args())
    except SystemExit:
        pass

    data = json.loads(captured.getvalue())
    assert data["vec"]["initialized"] is False
    assert "embed_model" not in data["vec"]
