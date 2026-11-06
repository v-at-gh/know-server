"""SQLite + FTS5 for content indexing and search."""
from __future__ import annotations

import json
import logging
import sqlite3
import sqlite_vec
from pathlib import Path

from .io_utils import load_json
from .config import DOMAINS, OLLAMA_EMBED_MODEL, EMBED_DIMENSIONS
from quizkb.client import OllamaClient

log = logging.getLogger(__name__)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS domains (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL DEFAULT 'unknown'
);

CREATE TABLE IF NOT EXISTS domain_translations (
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    lang TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (domain_id, lang)
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    "order" INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS campaign_translations (
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    lang TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (campaign_id, lang)
);

CREATE TABLE IF NOT EXISTS campaign_domains (
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (campaign_id, domain_id)
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    uuid TEXT UNIQUE NOT NULL,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    slug TEXT NOT NULL,
    "order" INTEGER NOT NULL DEFAULT 0,
    owner_user_id INTEGER,
    UNIQUE (domain_id, slug)
);

CREATE TABLE IF NOT EXISTS article_translations (
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    lang TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (article_id, lang)
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    uuid TEXT UNIQUE NOT NULL,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL,
    difficulty INTEGER NOT NULL DEFAULT 1,
    owner_user_id INTEGER
);

CREATE TABLE IF NOT EXISTS question_translations (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    lang TEXT NOT NULL,
    text TEXT NOT NULL,
    answer TEXT NOT NULL,
    distractors_json TEXT NOT NULL DEFAULT '[]',
    hints_json TEXT NOT NULL DEFAULT '[]',
    answer_why TEXT,
    PRIMARY KEY (question_id, lang)
);

CREATE TABLE IF NOT EXISTS packs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    campaigns_json TEXT NOT NULL DEFAULT '[]',
    domains_json TEXT NOT NULL DEFAULT '[]',
    languages_json TEXT NOT NULL DEFAULT '[]'
);

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    lang UNINDEXED,
    article_id UNINDEXED,
    title,
    summary,
    content,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS questions_fts USING fts5(
    lang UNINDEXED,
    question_id UNINDEXED,
    text,
    answer,
    hints,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS article_embedding_index (
    uuid TEXT NOT NULL,
    lang TEXT NOT NULL,
    vec_rowid INTEGER NOT NULL UNIQUE,
    PRIMARY KEY (uuid, lang)
);

CREATE VIRTUAL TABLE IF NOT EXISTS article_embeddings USING vec0(
    embedding float[128] distance_metric=cosine
);

CREATE TABLE IF NOT EXISTS question_embedding_index (
    uuid TEXT NOT NULL,
    lang TEXT NOT NULL,
    vec_rowid INTEGER NOT NULL UNIQUE,
    PRIMARY KEY (uuid, lang)
);

CREATE VIRTUAL TABLE IF NOT EXISTS question_embeddings USING vec0(
    embedding float[128] distance_metric=cosine
);
"""

def _get_domain_id(cursor: sqlite3.Cursor, slug: str, kind: str) -> int:
    cursor.execute("SELECT id FROM domains WHERE slug = ?", (slug,))
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute("INSERT INTO domains (slug, kind) VALUES (?, ?)", (slug, kind))
    return cursor.lastrowid

def _get_campaign_id(cursor: sqlite3.Cursor, slug: str) -> int:
    cursor.execute("SELECT id FROM campaigns WHERE slug = ?", (slug,))
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute("INSERT INTO campaigns (slug) VALUES (?)", (slug,))
    return cursor.lastrowid

def _migrate_content_db(conn: sqlite3.Connection) -> None:
    def _col_exists(table: str, col: str) -> bool:
        return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})").fetchall())
    if not _col_exists("articles", "owner_user_id"):
        conn.execute("ALTER TABLE articles ADD COLUMN owner_user_id INTEGER")
    if not _col_exists("questions", "owner_user_id"):
        conn.execute("ALTER TABLE questions ADD COLUMN owner_user_id INTEGER")
    conn.commit()


def sync_all(db_path: Path, output_root: Path, packs_json_path: Path, *, reembed: bool = False) -> None:
    log.info(f"Syncing DB at {db_path}...")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate_content_db(conn)
    cursor = conn.cursor()

    # Clear all tables (except schema)
    for table in ["domains", "campaigns", "articles", "questions", "packs"]:
        cursor.execute(f"DELETE FROM {table}")
    cursor.execute("DELETE FROM articles_fts")
    cursor.execute("DELETE FROM questions_fts")

    # 1. Domains
    kinds_by_slug = {d["slug"]: d.get("kind", "unknown") for d in DOMAINS}
    domain_id_map = {}
    
    datasets_dir = output_root / "datasets"
    if datasets_dir.is_dir():
        for lang_dir in datasets_dir.iterdir():
            if not lang_dir.is_dir(): continue
            lang = lang_dir.name
            domains_data = load_json(lang_dir / "domains.json", []) or []
            for d in domains_data:
                slug = d["slug"]
                kind = kinds_by_slug.get(slug, "unknown")
                did = _get_domain_id(cursor, slug, kind)
                domain_id_map[slug] = did
                cursor.execute(
                    "INSERT OR REPLACE INTO domain_translations (domain_id, lang, name, description) VALUES (?, ?, ?, ?)",
                    (did, lang, d.get("name", slug), d.get("description", ""))
                )

    # 2. Campaigns
    campaign_id_map = {}
    if datasets_dir.is_dir():
        for lang_dir in datasets_dir.iterdir():
            if not lang_dir.is_dir(): continue
            lang = lang_dir.name
            campaigns_data = load_json(lang_dir / "campaigns.json", []) or []
            for c in campaigns_data:
                slug = c["slug"]
                cid = _get_campaign_id(cursor, slug)
                campaign_id_map[slug] = cid
                
                cursor.execute(
                    "INSERT OR REPLACE INTO campaign_translations (campaign_id, lang, name, description) VALUES (?, ?, ?, ?)",
                    (cid, lang, c.get("name", slug), c.get("description", ""))
                )
                cursor.execute("UPDATE campaigns SET \"order\" = ? WHERE id = ?", (c.get("order", 0), cid))
                
                # Link domains
                for pos, ds in enumerate(c.get("domainSlugs", [])):
                    did = domain_id_map.get(ds)
                    if did:
                        cursor.execute(
                            "INSERT OR REPLACE INTO campaign_domains (campaign_id, domain_id, position) VALUES (?, ?, ?)",
                            (cid, did, pos)
                        )

    # 3. Articles
    article_id_map = {} # (domain_slug, slug) -> article_id
    knowledge_dir = output_root / "knowledge"
    if knowledge_dir.is_dir():
        for lang_dir in knowledge_dir.iterdir():
            if not lang_dir.is_dir(): continue
            lang = lang_dir.name
            index_data = load_json(lang_dir / "index.json", []) or []
            
            # Syllabus has UUIDs, index has titles/descriptions
            syllabus_dir = output_root / "syllabus" / lang
            syllabus_uuids = {}
            if syllabus_dir.is_dir():
                for syl_file in syllabus_dir.glob("*.json"):
                    ds = syl_file.stem
                    syl_data = load_json(syl_file, []) or []
                    for item in syl_data:
                        syllabus_uuids[(ds, item["slug"])] = item.get("uuid")

            for item in index_data:
                ds = item["domainSlug"]
                slug = item["slug"].split("/")[-1]
                uuid = item.get("uuid") or syllabus_uuids.get((ds, slug))
                if not uuid:
                    continue
                
                did = domain_id_map.get(ds)
                if not did:
                    continue
                
                cursor.execute("SELECT id FROM articles WHERE uuid = ?", (uuid,))
                row = cursor.fetchone()
                if row:
                    aid = row[0]
                else:
                    cursor.execute(
                        "INSERT INTO articles (uuid, domain_id, slug, \"order\") VALUES (?, ?, ?, ?)",
                        (uuid, did, slug, item.get("order", 0))
                    )
                    aid = cursor.lastrowid
                
                article_id_map[(ds, slug)] = aid
                
                md_path = lang_dir / item.get("path", f"{ds}/{slug}.md")
                content = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
                
                cursor.execute(
                    "INSERT OR REPLACE INTO article_translations (article_id, lang, title, summary, content) VALUES (?, ?, ?, ?, ?)",
                    (aid, lang, item.get("title", slug), item.get("description", ""), content)
                )
                cursor.execute(
                    "INSERT INTO articles_fts (lang, article_id, title, summary, content) VALUES (?, ?, ?, ?, ?)",
                    (lang, aid, item.get("title", slug), item.get("description", ""), content)
                )

    # 4. Questions
    if datasets_dir.is_dir():
        for lang_dir in datasets_dir.iterdir():
            if not lang_dir.is_dir(): continue
            lang = lang_dir.name
            for q_file in lang_dir.glob("*.json"):
                if q_file.name in ("domains.json", "campaigns.json"): continue
                ds = q_file.stem
                did = domain_id_map.get(ds)
                if not did: continue
                
                questions = load_json(q_file, []) or []
                for q in questions:
                    if q.get("archived"):
                        continue
                    uuid = q.get("uuid")
                    if not uuid: continue
                    
                    rel = q.get("relatedArticleSlug", "")
                    aid = None
                    if "/" in rel:
                        art_slug = rel.split("/")[-1]
                        aid = article_id_map.get((ds, art_slug))
                    
                    cursor.execute("SELECT id FROM questions WHERE uuid = ?", (uuid,))
                    row = cursor.fetchone()
                    if row:
                        qid = row[0]
                    else:
                        cursor.execute(
                            "INSERT INTO questions (uuid, domain_id, article_id, difficulty) VALUES (?, ?, ?, ?)",
                            (uuid, did, aid, q.get("difficulty", 1))
                        )
                        qid = cursor.lastrowid
                    
                    hints_text = " ".join(q.get("hints", []))
                    dist_json = json.dumps(q.get("distractors", []), ensure_ascii=False)
                    hints_json = json.dumps(q.get("hints", []), ensure_ascii=False)
                    
                    cursor.execute(
                        "INSERT OR REPLACE INTO question_translations (question_id, lang, text, answer, distractors_json, hints_json, answer_why) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (qid, lang, q.get("text", ""), q.get("answer", ""), dist_json, hints_json, q.get("answerWhy"))
                    )
                    cursor.execute(
                        "INSERT INTO questions_fts (lang, question_id, text, answer, hints) VALUES (?, ?, ?, ?, ?)",
                        (lang, qid, q.get("text", ""), q.get("answer", ""), hints_text)
                    )

    # 5. Packs
    if packs_json_path.exists():
        try:
            specs = json.loads(packs_json_path.read_text(encoding="utf-8")).get("packs", [])
            for p in specs:
                cursor.execute(
                    "INSERT OR REPLACE INTO packs (id, kind, campaigns_json, domains_json, languages_json) VALUES (?, ?, ?, ?, ?)",
                    (
                        p["id"], p.get("kind", "campaign"),
                        json.dumps(p.get("campaigns", [])),
                        json.dumps(p.get("domains", [])),
                        json.dumps(p.get("languages", []))
                    )
                )
        except Exception as e:
            log.error(f"Failed to sync packs.json: {e}")

    conn.commit()

    if reembed:
        client = OllamaClient()
        cursor.execute("""
            SELECT a.uuid, at.lang, at.title, at.summary, at.content
            FROM articles a
            JOIN article_translations at ON at.article_id = a.id
        """)
        for uuid, lang, title, summary, content in cursor.fetchall():
            text = f"Title: {title}\nSummary: {summary}\n\nContent:\n{content}"
            if len(text) > 8192: text = text[:8192]
            rec = client.embed_batch([text])
            if not rec.error and rec.embeddings:
                emb = rec.embeddings[0]
                cursor.execute("SELECT vec_rowid FROM article_embedding_index WHERE uuid = ? AND lang = ?", (uuid, lang))
                idx_row = cursor.fetchone()
                if idx_row:
                    vrow = idx_row[0]
                    cursor.execute("DELETE FROM article_embeddings WHERE rowid = ?", (vrow,))
                    cursor.execute("INSERT INTO article_embeddings (rowid, embedding) VALUES (?, ?)", (vrow, sqlite3.Binary(sqlite_vec.serialize_float32(emb))))
                else:
                    cursor.execute("INSERT INTO article_embeddings (embedding) VALUES (?)", (sqlite3.Binary(sqlite_vec.serialize_float32(emb)),))
                    vrow = cursor.lastrowid
                    cursor.execute("INSERT INTO article_embedding_index (uuid, lang, vec_rowid) VALUES (?, ?, ?)", (uuid, lang, vrow))

        cursor.execute("""
            SELECT q.uuid, qt.lang, qt.text, qt.answer, qt.hints_json
            FROM questions q
            JOIN question_translations qt ON qt.question_id = q.id
        """)
        for uuid, lang, q_text, answer, hints_json in cursor.fetchall():
            hints_list = json.loads(hints_json) if hints_json else []
            hints_text = " ".join(hints_list)
            text = f"Question: {q_text}\nAnswer: {answer}\nHints: {hints_text}"
            if len(text) > 8192: text = text[:8192]
            rec = client.embed_batch([text])
            if not rec.error and rec.embeddings:
                emb = rec.embeddings[0]
                cursor.execute("SELECT vec_rowid FROM question_embedding_index WHERE uuid = ? AND lang = ?", (uuid, lang))
                idx_row = cursor.fetchone()
                if idx_row:
                    vrow = idx_row[0]
                    cursor.execute("DELETE FROM question_embeddings WHERE rowid = ?", (vrow,))
                    cursor.execute("INSERT INTO question_embeddings (rowid, embedding) VALUES (?, ?)", (vrow, sqlite3.Binary(sqlite_vec.serialize_float32(emb))))
                else:
                    cursor.execute("INSERT INTO question_embeddings (embedding) VALUES (?)", (sqlite3.Binary(sqlite_vec.serialize_float32(emb)),))
                    vrow = cursor.lastrowid
                    cursor.execute("INSERT INTO question_embedding_index (uuid, lang, vec_rowid) VALUES (?, ?, ?)", (uuid, lang, vrow))

        cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_model', ?)", (OLLAMA_EMBED_MODEL,))
        cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_dimensions', ?)", (str(EMBED_DIMENSIONS),))
        conn.commit()

    conn.close()
    log.info("DB sync complete.")

def sync_domain(db_path: Path, output_root: Path, lang: str, domain_slug: str, *, reembed: bool = False) -> None:
    log.info(f"Syncing domain {domain_slug} ({lang}) at {db_path}...")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate_content_db(conn)
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM domains WHERE slug = ?", (domain_slug,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
    domain_id = row[0]

    cursor.execute(
        "DELETE FROM question_translations WHERE lang = ? AND question_id IN (SELECT id FROM questions WHERE domain_id = ?)",
        (lang, domain_id)
    )
    cursor.execute(
        "DELETE FROM questions_fts WHERE lang = ? AND question_id IN (SELECT id FROM questions WHERE domain_id = ?)",
        (lang, domain_id)
    )

    cursor.execute("SELECT id, slug FROM articles WHERE domain_id = ?", (domain_id,))
    art_map = {row[1]: row[0] for row in cursor.fetchall()}

    json_path = output_root / "datasets" / lang / f"{domain_slug}.json"
    questions = load_json(json_path, []) or []
    
    for q in questions:
        if q.get("archived"):
            continue
        uuid = q.get("uuid")
        if not uuid:
            continue
        
        rel = q.get("relatedArticleSlug", "")
        aid = None
        if "/" in rel:
            art_slug = rel.split("/")[-1]
            aid = art_map.get(art_slug)
        
        cursor.execute("SELECT id FROM questions WHERE uuid = ?", (uuid,))
        row = cursor.fetchone()
        if row:
            qid = row[0]
            cursor.execute(
                "UPDATE questions SET domain_id = ?, article_id = ?, difficulty = ? WHERE id = ?",
                (domain_id, aid, q.get("difficulty", 1), qid)
            )
        else:
            cursor.execute(
                "INSERT INTO questions (uuid, domain_id, article_id, difficulty) VALUES (?, ?, ?, ?)",
                (uuid, domain_id, aid, q.get("difficulty", 1))
            )
            qid = cursor.lastrowid
        
        hints_text = " ".join(q.get("hints", []))
        dist_json = json.dumps(q.get("distractors", []), ensure_ascii=False)
        hints_json = json.dumps(q.get("hints", []), ensure_ascii=False)
        
        cursor.execute(
            "INSERT OR REPLACE INTO question_translations (question_id, lang, text, answer, distractors_json, hints_json, answer_why) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (qid, lang, q.get("text", ""), q.get("answer", ""), dist_json, hints_json, q.get("answerWhy"))
        )
        cursor.execute(
            "INSERT INTO questions_fts (lang, question_id, text, answer, hints) VALUES (?, ?, ?, ?, ?)",
            (lang, qid, q.get("text", ""), q.get("answer", ""), hints_text)
        )

    conn.commit()

    if reembed:
        client = OllamaClient()
        cursor.execute("""
            SELECT q.uuid, qt.lang, qt.text, qt.answer, qt.hints_json
            FROM questions q
            JOIN question_translations qt ON qt.question_id = q.id
            WHERE q.domain_id = ? AND qt.lang = ?
        """, (domain_id, lang))
        for uuid, lang_code, q_text, answer, hints_json in cursor.fetchall():
            hints_list = json.loads(hints_json) if hints_json else []
            hints_text = " ".join(hints_list)
            text = f"Question: {q_text}\nAnswer: {answer}\nHints: {hints_text}"
            if len(text) > 8192: text = text[:8192]
            rec = client.embed_batch([text])
            if not rec.error and rec.embeddings:
                emb = rec.embeddings[0]
                cursor.execute("SELECT vec_rowid FROM question_embedding_index WHERE uuid = ? AND lang = ?", (uuid, lang_code))
                idx_row = cursor.fetchone()
                if idx_row:
                    vrow = idx_row[0]
                    cursor.execute("DELETE FROM question_embeddings WHERE rowid = ?", (vrow,))
                    cursor.execute("INSERT INTO question_embeddings (rowid, embedding) VALUES (?, ?)", (vrow, sqlite3.Binary(sqlite_vec.serialize_float32(emb))))
                else:
                    cursor.execute("INSERT INTO question_embeddings (embedding) VALUES (?)", (sqlite3.Binary(sqlite_vec.serialize_float32(emb)),))
                    vrow = cursor.lastrowid
                    cursor.execute("INSERT INTO question_embedding_index (uuid, lang, vec_rowid) VALUES (?, ?, ?)", (uuid, lang_code, vrow))

        cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_model', ?)", (OLLAMA_EMBED_MODEL,))
        cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_dimensions', ?)", (str(EMBED_DIMENSIONS),))
        conn.commit()

    conn.close()
    log.info(f"Domain sync complete for {domain_slug} ({lang}).")

def _classify_embed_error(error: str | None) -> str:
    if not error:
        return "unknown_embed"
    e = error.lower()
    if "connection" in e or "refused" in e:
        return "connection"
    if "timeout" in e or "timed out" in e:
        return "timeout"
    if "http" in e or "status" in e or "response" in e:
        return "http"
    return "unknown_embed"

def reindex_article(db_path: Path, uuid: str, lang: str, client) -> tuple[bool, str]:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT at.title, at.summary, at.content
            FROM articles a
            JOIN article_translations at ON at.article_id = a.id
            WHERE a.uuid = ? AND at.lang = ?
        """, (uuid, lang))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return False, "no_translation"
        title, summary, content = row
        text = f"Title: {title}\nSummary: {summary}\n\nContent:\n{content}"
        if len(text) > 8192:
            text = text[:8192]

        rec = client.embed_batch([text])
        if rec.error or not rec.embeddings:
            log.warning(f"reindex_article embed failed for {uuid} ({lang}): {rec.error}")
            return False, _classify_embed_error(rec.error)

        embedding = rec.embeddings[0]

        conn = sqlite3.connect(db_path)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        cursor = conn.cursor()

        cursor.execute("SELECT vec_rowid FROM article_embedding_index WHERE uuid = ? AND lang = ?", (uuid, lang))
        idx_row = cursor.fetchone()
        if idx_row:
            vec_rowid = idx_row[0]
            cursor.execute("DELETE FROM article_embeddings WHERE rowid = ?", (vec_rowid,))
            cursor.execute("INSERT INTO article_embeddings (rowid, embedding) VALUES (?, ?)", (vec_rowid, sqlite3.Binary(sqlite_vec.serialize_float32(embedding))))
        else:
            cursor.execute("INSERT INTO article_embeddings (embedding) VALUES (?)", (sqlite3.Binary(sqlite_vec.serialize_float32(embedding)),))
            vec_rowid = cursor.lastrowid
            cursor.execute("INSERT INTO article_embedding_index (uuid, lang, vec_rowid) VALUES (?, ?, ?)", (uuid, lang, vec_rowid))

        cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_model', ?)", (OLLAMA_EMBED_MODEL,))
        cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_dimensions', ?)", (str(EMBED_DIMENSIONS),))

        conn.commit()
        conn.close()
        return True, ""
    except Exception as e:
        log.error(f"reindex_article error for {uuid} ({lang}): {e}")
        return False, "db"

def reindex_question(db_path: Path, uuid: str, lang: str, client) -> tuple[bool, str]:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT qt.text, qt.answer, qt.hints_json
            FROM questions q
            JOIN question_translations qt ON qt.question_id = q.id
            WHERE q.uuid = ? AND qt.lang = ?
        """, (uuid, lang))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return False, "no_translation"
        q_text, answer, hints_json = row
        hints_list = json.loads(hints_json) if hints_json else []
        hints_text = " ".join(hints_list)
        text = f"Question: {q_text}\nAnswer: {answer}\nHints: {hints_text}"
        if len(text) > 8192:
            text = text[:8192]

        rec = client.embed_batch([text])
        if rec.error or not rec.embeddings:
            log.warning(f"reindex_question embed failed for {uuid} ({lang}): {rec.error}")
            return False, _classify_embed_error(rec.error)

        embedding = rec.embeddings[0]

        conn = sqlite3.connect(db_path)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        cursor = conn.cursor()

        cursor.execute("SELECT vec_rowid FROM question_embedding_index WHERE uuid = ? AND lang = ?", (uuid, lang))
        idx_row = cursor.fetchone()
        if idx_row:
            vec_rowid = idx_row[0]
            cursor.execute("DELETE FROM question_embeddings WHERE rowid = ?", (vec_rowid,))
            cursor.execute("INSERT INTO question_embeddings (rowid, embedding) VALUES (?, ?)", (vec_rowid, sqlite3.Binary(sqlite_vec.serialize_float32(embedding))))
        else:
            cursor.execute("INSERT INTO question_embeddings (embedding) VALUES (?)", (sqlite3.Binary(sqlite_vec.serialize_float32(embedding)),))
            vec_rowid = cursor.lastrowid
            cursor.execute("INSERT INTO question_embedding_index (uuid, lang, vec_rowid) VALUES (?, ?, ?)", (uuid, lang, vec_rowid))

        cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_model', ?)", (OLLAMA_EMBED_MODEL,))
        cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_dimensions', ?)", (str(EMBED_DIMENSIONS),))

        conn.commit()
        conn.close()
        return True, ""
    except Exception as e:
        log.error(f"reindex_question error for {uuid} ({lang}): {e}")
        return False, "db"

def prune_orphan_embeddings(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    cursor = conn.cursor()

    # 1. Prune index rows not pointing to valid translations
    cursor.execute("DELETE FROM article_embedding_index WHERE (uuid, lang) NOT IN (SELECT a.uuid, at.lang FROM articles a JOIN article_translations at ON at.article_id = a.id UNION SELECT '', '')")
    art_idx_del = cursor.rowcount

    cursor.execute("DELETE FROM question_embedding_index WHERE (uuid, lang) NOT IN (SELECT q.uuid, qt.lang FROM questions q JOIN question_translations qt ON qt.question_id = q.id UNION SELECT '', '')")
    q_idx_del = cursor.rowcount

    # 2. Prune vec rows not referenced by index
    cursor.execute("SELECT rowid FROM article_embeddings WHERE rowid NOT IN (SELECT vec_rowid FROM article_embedding_index UNION SELECT -1)")
    art_orphans = [r[0] for r in cursor.fetchall()]
    art_vec_del = 0
    for r_id in art_orphans:
        cursor.execute("DELETE FROM article_embeddings WHERE rowid = ?", (r_id,))
        art_vec_del += 1

    cursor.execute("SELECT rowid FROM question_embeddings WHERE rowid NOT IN (SELECT vec_rowid FROM question_embedding_index UNION SELECT -1)")
    q_orphans = [r[0] for r in cursor.fetchall()]
    q_vec_del = 0
    for r_id in q_orphans:
        cursor.execute("DELETE FROM question_embeddings WHERE rowid = ?", (r_id,))
        q_vec_del += 1

    conn.commit()
    conn.close()
    return {"articles_deleted": art_vec_del + art_idx_del, "questions_deleted": q_vec_del + q_idx_del}

def count_missing_embeddings(db_path: Path) -> dict:
    """Read-only. Returns {
        'articles_missing': N, 'articles_total': N,
        'questions_missing': M, 'questions_total': M,
    }."""
    if not db_path.exists():
        return {'articles_missing': 0, 'articles_total': 0, 'questions_missing': 0, 'questions_total': 0}
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        tbls = {r[0] for r in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        def _cnt(t):
            if t not in tbls:
                return 0
            return cursor.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

        art_total = _cnt("article_translations")
        q_total = _cnt("question_translations")

        # If index tables absent -> everything missing
        if "article_embedding_index" not in tbls or "question_embedding_index" not in tbls:
            return {
                'articles_missing': art_total, 'articles_total': art_total,
                'questions_missing': q_total, 'questions_total': q_total,
            }

        # Existing LEFT JOIN path for present schema
        cursor.execute("""
            SELECT COUNT(*), COUNT(idx.vec_rowid)
            FROM articles a
            JOIN article_translations at ON at.article_id = a.id
            LEFT JOIN article_embedding_index idx ON idx.uuid = a.uuid AND idx.lang = at.lang
        """)
        art_row = cursor.fetchone()
        art_indexed = art_row[1] if art_row else 0

        cursor.execute("""
            SELECT COUNT(*), COUNT(idx.vec_rowid)
            FROM questions q
            JOIN question_translations qt ON qt.question_id = q.id
            LEFT JOIN question_embedding_index idx ON idx.uuid = q.uuid AND idx.lang = qt.lang
        """)
        q_row = cursor.fetchone()
        q_indexed = q_row[1] if q_row else 0

        return {
            'articles_missing': art_total - art_indexed,
            'articles_total': art_total,
            'questions_missing': q_total - q_indexed,
            'questions_total': q_total,
        }
    except sqlite3.OperationalError:
        return {'articles_missing': 0, 'articles_total': 0, 'questions_missing': 0, 'questions_total': 0}
    finally:
        conn.close()

def list_missing_article_keys(db_path: Path) -> list[tuple[str, str]]:
    """Returns [(uuid, lang), ...] for article translations missing embeddings. Read-only."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT a.uuid, at.lang
            FROM articles a
            JOIN article_translations at ON at.article_id = a.id
            LEFT JOIN article_embedding_index idx ON idx.uuid = a.uuid AND idx.lang = at.lang
            WHERE idx.vec_rowid IS NULL
        """)
        return [(row[0], row[1]) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

def list_missing_question_keys(db_path: Path) -> list[tuple[str, str]]:
    """Returns [(uuid, lang), ...] for question translations missing embeddings. Read-only."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT q.uuid, qt.lang
            FROM questions q
            JOIN question_translations qt ON qt.question_id = q.id
            LEFT JOIN question_embedding_index idx ON idx.uuid = q.uuid AND idx.lang = qt.lang
            WHERE idx.vec_rowid IS NULL
        """)
        return [(row[0], row[1]) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

def list_all_article_keys(db_path: Path) -> list[tuple[str, str]]:
    """Return every article translation key, whether or not it has an embedding."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("""
            SELECT a.uuid, at.lang
            FROM articles a
            JOIN article_translations at ON at.article_id = a.id
        """)
        return [(row[0], row[1]) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

def list_all_question_keys(db_path: Path) -> list[tuple[str, str]]:
    """Return every question translation key, whether or not it has an embedding."""
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("""
            SELECT q.uuid, qt.lang
            FROM questions q
            JOIN question_translations qt ON qt.question_id = q.id
        """)
        return [(row[0], row[1]) for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

def search_articles(db_path: Path, query: str, lang: str | None = None) -> list[dict]:
    if not db_path.exists(): return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = """
            SELECT a.uuid, a.slug, d.slug as domain_slug, at.lang, at.title, at.summary,
                   snippet(articles_fts, 4, '<mark>', '</mark>', '…', 30) as snippet,
                   bm25(articles_fts) as score
            FROM articles_fts
            JOIN articles a ON a.id = articles_fts.article_id
            JOIN domains d ON d.id = a.domain_id
            JOIN article_translations at ON at.article_id = a.id AND at.lang = articles_fts.lang
            WHERE articles_fts MATCH ?
        """
        params = [query]
        if lang:
            sql += " AND articles_fts.lang = ?"
            params.append(lang)
        sql += " ORDER BY score LIMIT 50"
        
        cursor = conn.execute(sql, params)
        return [dict(r) for r in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

def search_questions(db_path: Path, query: str, lang: str | None = None) -> list[dict]:
    if not db_path.exists(): return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sql = """
            SELECT q.uuid, d.slug as domain_slug, qt.lang, qt.text, qt.answer,
                   snippet(questions_fts, 2, '<mark>', '</mark>', '…', 30) as snippet,
                   bm25(questions_fts) as score
            FROM questions_fts
            JOIN questions q ON q.id = questions_fts.question_id
            JOIN domains d ON d.id = q.domain_id
            JOIN question_translations qt ON qt.question_id = q.id AND qt.lang = questions_fts.lang
            WHERE questions_fts MATCH ?
        """
        params = [query]
        if lang:
            sql += " AND questions_fts.lang = ?"
            params.append(lang)
        sql += " ORDER BY score LIMIT 50"
        
        cursor = conn.execute(sql, params)
        return [dict(r) for r in cursor.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

def search_articles_hybrid(
    db_path: Path,
    query: str,
    query_vec: bytes | None,
    lang: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Hybrid FTS5 + vec0 search via RRF (k=60).
    Note: FULL OUTER JOIN is natively supported in SQLite 3.39+ but we use UNION+LEFT JOIN for portability.
    """
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if query_vec is not None:
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except Exception as e:
            log.warning(f"Failed to load sqlite_vec in search_articles_hybrid: {e}")
            query_vec = None

    try:
        if query_vec is not None:
            sql = """
                WITH fts_matches AS (
                    SELECT article_id, lang,
                           bm25(articles_fts) AS bm25_score,
                           snippet(articles_fts, 4, '<mark>', '</mark>', '...', 30) AS snippet
                    FROM articles_fts
                    WHERE articles_fts MATCH :query
                      AND (:lang IS NULL OR lang = :lang)
                    ORDER BY bm25_score
                    LIMIT 50
                ),
                fts_ranked AS (
                    SELECT article_id, lang, snippet,
                           ROW_NUMBER() OVER (ORDER BY bm25_score) AS rank_fts
                    FROM fts_matches
                ),
                fts_keys AS (
                    SELECT a.uuid, f.lang, f.rank_fts, f.snippet
                    FROM fts_ranked f
                    JOIN articles a ON a.id = f.article_id
                ),
                vec_matches AS (
                    SELECT idx.uuid, idx.lang,
                           ROW_NUMBER() OVER (ORDER BY v.distance) AS rank_vec
                    FROM (
                        SELECT rowid, distance
                        FROM article_embeddings
                        WHERE embedding MATCH :query_vec
                          AND k = 50
                    ) v
                    JOIN article_embedding_index idx ON idx.vec_rowid = v.rowid
                    WHERE (:lang IS NULL OR idx.lang = :lang)
                ),
                all_keys AS (
                    SELECT uuid, lang FROM fts_keys
                    UNION
                    SELECT uuid, lang FROM vec_matches
                ),
                combined AS (
                    SELECT k.uuid, k.lang,
                           COALESCE(1.0 / (60 + f.rank_fts), 0.0)
                           + COALESCE(1.0 / (60 + v.rank_vec), 0.0) AS rrf_score,
                           f.snippet AS snippet
                    FROM all_keys k
                    LEFT JOIN fts_keys f ON f.uuid = k.uuid AND f.lang = k.lang
                    LEFT JOIN vec_matches v ON v.uuid = k.uuid AND v.lang = k.lang
                )
                SELECT a.uuid, a.slug, d.slug AS domain_slug, at.lang,
                       at.title, at.summary, c.rrf_score, c.snippet
                FROM combined c
                JOIN articles a ON a.uuid = c.uuid
                JOIN domains d ON d.id = a.domain_id
                JOIN article_translations at ON at.article_id = a.id AND at.lang = c.lang
                ORDER BY c.rrf_score DESC
                LIMIT :limit
            """
            cursor = conn.execute(sql, {"query": query, "query_vec": query_vec, "lang": lang, "limit": limit})
        else:
            sql = """
                WITH fts_matches AS (
                    SELECT article_id, lang,
                           snippet(articles_fts, 4, '<mark>', '</mark>', '...', 30) AS snippet,
                           bm25(articles_fts) AS score
                    FROM articles_fts
                    WHERE articles_fts MATCH :query
                      AND (:lang IS NULL OR lang = :lang)
                    ORDER BY score
                    LIMIT :limit
                )
                SELECT a.uuid, a.slug, d.slug AS domain_slug, at.lang,
                       at.title, at.summary, f.snippet, 0.0 AS rrf_score
                FROM fts_matches f
                JOIN articles a ON a.id = f.article_id
                JOIN domains d ON d.id = a.domain_id
                JOIN article_translations at ON at.article_id = a.id AND at.lang = f.lang
                ORDER BY f.score
                LIMIT :limit
            """
            cursor = conn.execute(sql, {"query": query, "lang": lang, "limit": limit})

        results = [dict(r) for r in cursor.fetchall()]
        for row in results:
            if not row.get("snippet"):
                summary = row.get("summary") or ""
                row["snippet"] = (summary[:150] + "...") if len(summary) > 150 else summary
        return results
    except sqlite3.OperationalError as e:
        log.warning(f"search_articles_hybrid error: {e}")
        return []
    finally:
        conn.close()

def search_questions_hybrid(
    db_path: Path,
    query: str,
    query_vec: bytes | None,
    lang: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Hybrid FTS5 + vec0 search via RRF (k=60).
    Note: FULL OUTER JOIN is natively supported in SQLite 3.39+ but we use UNION+LEFT JOIN for portability.
    """
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if query_vec is not None:
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except Exception as e:
            log.warning(f"Failed to load sqlite_vec in search_questions_hybrid: {e}")
            query_vec = None

    try:
        if query_vec is not None:
            sql = """
                WITH fts_matches AS (
                    SELECT question_id, lang,
                           bm25(questions_fts) AS bm25_score,
                           snippet(questions_fts, 2, '<mark>', '</mark>', '...', 30) AS snippet
                    FROM questions_fts
                    WHERE questions_fts MATCH :query
                      AND (:lang IS NULL OR lang = :lang)
                    ORDER BY bm25_score
                    LIMIT 50
                ),
                fts_ranked AS (
                    SELECT question_id, lang, snippet,
                           ROW_NUMBER() OVER (ORDER BY bm25_score) AS rank_fts
                    FROM fts_matches
                ),
                fts_keys AS (
                    SELECT q.uuid, f.lang, f.rank_fts, f.snippet
                    FROM fts_ranked f
                    JOIN questions q ON q.id = f.question_id
                ),
                vec_matches AS (
                    SELECT idx.uuid, idx.lang,
                           ROW_NUMBER() OVER (ORDER BY v.distance) AS rank_vec
                    FROM (
                        SELECT rowid, distance
                        FROM question_embeddings
                        WHERE embedding MATCH :query_vec
                          AND k = 50
                    ) v
                    JOIN question_embedding_index idx ON idx.vec_rowid = v.rowid
                    WHERE (:lang IS NULL OR idx.lang = :lang)
                ),
                all_keys AS (
                    SELECT uuid, lang FROM fts_keys
                    UNION
                    SELECT uuid, lang FROM vec_matches
                ),
                combined AS (
                    SELECT k.uuid, k.lang,
                           COALESCE(1.0 / (60 + f.rank_fts), 0.0)
                           + COALESCE(1.0 / (60 + v.rank_vec), 0.0) AS rrf_score,
                           f.snippet AS snippet
                    FROM all_keys k
                    LEFT JOIN fts_keys f ON f.uuid = k.uuid AND f.lang = k.lang
                    LEFT JOIN vec_matches v ON v.uuid = k.uuid AND v.lang = k.lang
                )
                SELECT q.uuid, d.slug AS domain_slug, qt.lang,
                       qt.text, qt.answer, c.rrf_score, c.snippet
                FROM combined c
                JOIN questions q ON q.uuid = c.uuid
                JOIN domains d ON d.id = q.domain_id
                JOIN question_translations qt ON qt.question_id = q.id AND qt.lang = c.lang
                ORDER BY c.rrf_score DESC
                LIMIT :limit
            """
            cursor = conn.execute(sql, {"query": query, "query_vec": query_vec, "lang": lang, "limit": limit})
        else:
            sql = """
                SELECT q.uuid, d.slug AS domain_slug, qt.lang,
                       qt.text, qt.answer,
                       snippet(questions_fts, 2, '<mark>', '</mark>', '...', 30) AS snippet,
                       0.0 AS rrf_score
                FROM questions_fts
                JOIN questions q ON q.id = questions_fts.question_id
                JOIN domains d ON d.id = q.domain_id
                JOIN question_translations qt ON qt.question_id = q.id
                                            AND qt.lang = questions_fts.lang
                WHERE questions_fts MATCH :query
                  AND (:lang IS NULL OR qt.lang = :lang)
                ORDER BY bm25(questions_fts)
                LIMIT :limit
            """
            cursor = conn.execute(sql, {"query": query, "lang": lang, "limit": limit})

        results = [dict(r) for r in cursor.fetchall()]
        for row in results:
            if not row.get("snippet"):
                text = row.get("text") or ""
                row["snippet"] = (text[:150] + "...") if len(text) > 150 else text
        return results
    except sqlite3.OperationalError as e:
        log.warning(f"search_questions_hybrid error: {e}")
        return []
    finally:
        conn.close()
