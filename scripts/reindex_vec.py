#!/usr/bin/env python3
"""CLI tool to manage vector embeddings in content.db"""
import argparse
import datetime
import json
from pathlib import Path
import sqlite3
import sys
import time
import signal
import sqlite_vec

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from quizkb import db as kb_db
from quizkb.client import OllamaClient, OLLAMA_EMBED_MODEL, EMBED_DIMENSIONS
from quizkb.observability import configure_telemetry, get_meter, safe_exit

DB_PATH = REPO_ROOT / "state" / "content.db"

def vlog(args, msg: str):
    if getattr(args, "verbose", 0) >= 1:
        print(f"[debug] {msg}", file=sys.stderr)

def _ensure_schema(target_db: Path) -> None:
    conn = sqlite3.connect(target_db)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.executescript(kb_db.SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _reindex_batch(target_db: Path, items: list[tuple[str, str]], is_article: bool, client, args, progress_ctx, ollama_errors, pending: bool = False) -> tuple[int, int]:
    if not items:
        return 0, 0

    from quizkb.config import EMBED_BATCH_SIZE, EMBED_DIMENSIONS
    OLLAMA_ERROR_CLASSES = frozenset({"connection", "timeout", "http", "unknown_embed"})

    errors = 0
    processed = 0

    for i in range(0, len(items), EMBED_BATCH_SIZE):
        batch = items[i:i + EMBED_BATCH_SIZE]
        texts = []
        valid_items = []
        conn = sqlite3.connect(target_db)
        cursor = conn.cursor()
        for uuid, lang in batch:
            if is_article:
                cursor.execute("""
                    SELECT at.title, at.summary, at.content
                    FROM articles a
                    JOIN article_translations at ON at.article_id = a.id
                    WHERE a.uuid = ? AND at.lang = ?
                """, (uuid, lang))
                row = cursor.fetchone()
                if row:
                    title, summary, content = row
                    text = f"Title: {title}\nSummary: {summary}\n\nContent:\n{content}"
                    if len(text) > 8192: text = text[:8192]
                    texts.append(text)
                    valid_items.append((uuid, lang))
            else:
                cursor.execute("""
                    SELECT qt.text, qt.answer, qt.hints_json
                    FROM questions q
                    JOIN question_translations qt ON qt.question_id = q.id
                    WHERE q.uuid = ? AND qt.lang = ?
                """, (uuid, lang))
                row = cursor.fetchone()
                if row:
                    q_text, answer, hints_json = row
                    hints_list = json.loads(hints_json) if hints_json else []
                    hints_text = " ".join(hints_list)
                    text = f"Question: {q_text}\nAnswer: {answer}\nHints: {hints_text}"
                    if len(text) > 8192: text = text[:8192]
                    texts.append(text)
                    valid_items.append((uuid, lang))
        conn.close()

        if not texts:
            continue

        t0 = time.time()
        rec = client.embed_batch(texts)
        dt = (time.time() - t0) * 1000

        if rec.error or not rec.embeddings or len(rec.embeddings) != len(texts):
            err_class = kb_db._classify_embed_error(rec.error)
            if err_class in OLLAMA_ERROR_CLASSES:
                ollama_errors.add(len(valid_items), {"model": args.model, "error_class": err_class})
            errors += len(valid_items)
            for uuid, lang in valid_items:
                vlog(args, f"{'reindex_article' if is_article else 'reindex_question'} uuid={uuid} lang={lang} -> FAIL in {dt:.1f}ms ({err_class})")
                processed += 1
                progress_ctx(processed, uuid)
        else:
            conn = sqlite3.connect(target_db)
            if not pending:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
            cursor = conn.cursor()

            for (uuid, lang), embedding in zip(valid_items, rec.embeddings):
                try:
                    blob = sqlite3.Binary(sqlite_vec.serialize_float32(embedding))
                    if pending:
                        tbl = "article_embeddings_pending" if is_article else "question_embeddings_pending"
                        cursor.execute(f"INSERT OR REPLACE INTO {tbl} (uuid, lang, embedding) VALUES (?, ?, ?)", (uuid, lang, blob))
                    else:
                        if is_article:
                            cursor.execute("SELECT vec_rowid FROM article_embedding_index WHERE uuid = ? AND lang = ?", (uuid, lang))
                        else:
                            cursor.execute("SELECT vec_rowid FROM question_embedding_index WHERE uuid = ? AND lang = ?", (uuid, lang))
                        idx_row = cursor.fetchone()
                        if idx_row:
                            vec_rowid = idx_row[0]
                            if is_article:
                                cursor.execute("DELETE FROM article_embeddings WHERE rowid = ?", (vec_rowid,))
                                cursor.execute("INSERT INTO article_embeddings (rowid, embedding) VALUES (?, ?)", (vec_rowid, blob))
                            else:
                                cursor.execute("DELETE FROM question_embeddings WHERE rowid = ?", (vec_rowid,))
                                cursor.execute("INSERT INTO question_embeddings (rowid, embedding) VALUES (?, ?)", (vec_rowid, blob))
                        else:
                            if is_article:
                                cursor.execute("INSERT INTO article_embeddings (embedding) VALUES (?)", (blob,))
                                vec_rowid = cursor.lastrowid
                                cursor.execute("INSERT INTO article_embedding_index (uuid, lang, vec_rowid) VALUES (?, ?, ?)", (uuid, lang, vec_rowid))
                            else:
                                cursor.execute("INSERT INTO question_embeddings (embedding) VALUES (?)", (blob,))
                                vec_rowid = cursor.lastrowid
                                cursor.execute("INSERT INTO question_embedding_index (uuid, lang, vec_rowid) VALUES (?, ?, ?)", (uuid, lang, vec_rowid))

                    vlog(args, f"{'reindex_article' if is_article else 'reindex_question'} uuid={uuid} lang={lang} -> ok (batched) in {dt:.1f}ms")
                except Exception as e:
                    errors += 1
                processed += 1
                progress_ctx(processed, uuid)

            if not pending:
                cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_model', ?)", (args.model,))
                cursor.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_dimensions', ?)", (str(EMBED_DIMENSIONS),))
            conn.commit()
            conn.close()

    return errors, processed


def main():
    parser = argparse.ArgumentParser(description="Reindex vector embeddings in content.db")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--full", action="store_true", help="Rebuild all vector embeddings from scratch")
    group.add_argument("--missing", action="store_true", help="Embed missing translations only")
    group.add_argument("--prune", action="store_true", help="Prune orphan vector embeddings")
    group.add_argument("--domain", type=str, help="Targeted domain for reindexing")

    parser.add_argument("--lang", type=str, help="Target language (only valid with --domain)")
    parser.add_argument("--model", type=str, default=OLLAMA_EMBED_MODEL, help="Embedding model name")
    parser.add_argument("--task-id", default=None, help="Task ID for progress.json writes (set by worker)")
    parser.add_argument("--dry-run", action="store_true", help="Show plan without calling Ollama or writing DB")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity")

    args = parser.parse_args()

    if args.lang and not args.domain:
        print("Error: --lang is only valid when --domain is specified.", file=sys.stderr)
        safe_exit(1)

    mode = "missing"
    if args.full: mode = "full"
    elif args.prune: mode = "prune"
    elif args.domain: mode = "domain"

    tot_items = 0

    task_id = args.task_id
    progress_file = None
    if task_id:
        task_dir = REPO_ROOT / "state" / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        progress_file = task_dir / "progress.json"


    def write_progress(done: int, total: int, current: str = "") -> None:
        if progress_file is None:
            return
        data = {"phase": "reindex", "done": done, "total": total, "current": current}
        progress_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    configure_telemetry("reindex")
    meter = get_meter("reindex")
    run_completed = meter.create_counter(
        "reindex_run_completed_total",
        description="Reindex runs completed, by mode and status",
    )
    coverage_gauge = meter.create_gauge(
        "reindex_current_coverage",
        description="Fraction of translations with embeddings, by kind",
    )
    progress_gauge = meter.create_gauge(
        "reindex_progress_ratio",
        description="Current run progress 0.0-1.0, by mode",
    )
    last_success = meter.create_gauge(
        "reindex_last_success_timestamp_seconds",
        description="Unix timestamp of last successful reindex, by mode",
    )
    duration_hist = meter.create_histogram(
        "reindex_duration_seconds",
        description="Reindex run duration in seconds, by mode",
        unit="s",
    )
    ollama_errors = meter.create_counter(
        "reindex_ollama_errors_total",
        description="Ollama embed failures during reindex, by model and error class",
    )
    OLLAMA_ERROR_CLASSES = frozenset({"connection", "timeout", "http", "unknown_embed"})

    run_start_time = time.time()

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    target_db = getattr(args, "db_path", DB_PATH)

    if args.dry_run:
        if not target_db.exists():
            print(f"Error: Database file {target_db} not found.", file=sys.stderr)
            safe_exit(1)

        counts = kb_db.count_missing_embeddings(target_db)
        art_missing = counts["articles_missing"]
        art_tot = counts["articles_total"]
        q_missing = counts["questions_missing"]
        q_tot = counts["questions_total"]

        if mode == "full":
            est_time = int((art_tot + q_tot) * 0.1)
            print("Dry run: reindex_vec --full")
            print(f"Article coverage: 0/{art_tot} indexed ({art_tot} missing on full rebuild)")
            print(f"Question coverage: 0/{q_tot} indexed ({q_tot} missing on full rebuild)")
            print(f"Estimated time: ~{est_time}s (~100ms per item)")
            print(f"Would write: {art_tot} article vectors, {q_tot} question vectors")
            print("Would update: schema_meta (embed_model, embed_dimensions)")
        elif mode == "missing":
            est_time = int((art_missing + q_missing) * 0.1)
            print("Dry run: reindex_vec --missing")
            print(f"Article coverage: {art_missing}/{art_tot} missing ({art_tot - art_missing}/{art_tot} indexed)")
            print(f"Question coverage: {q_missing}/{q_tot} missing ({q_tot - q_missing}/{q_tot} indexed)")
            print(f"Estimated time: ~{est_time}s (~100ms per item)")
            print(f"Would write: {art_missing} article vectors, {q_missing} question vectors")
            print("Would update: schema_meta (embed_model, embed_dimensions)")
        elif mode == "prune":
            print("Dry run: reindex_vec --prune")
            print("Would remove orphan vector embeddings and unreferenced index entries")
        elif mode == "domain":
            print(f"Dry run: reindex_vec --domain {args.domain} (lang={args.lang or 'all'})")
            print("Would reindex targeted articles and questions")

        safe_exit(0)

    if not target_db.exists():
        print(f"Error: Database file {target_db} not found.", file=sys.stderr)
        run_completed.add(1, {"mode": mode, "status": "failed"})
        safe_exit(1)

    if mode == "prune":
        vlog(args, "running prune_orphan_embeddings...")
        stats = kb_db.prune_orphan_embeddings(target_db)
        print(f"Pruned orphan embeddings: {stats}")
        print("=== Reindex Completed Successfully ===")
        run_completed.add(1, {"mode": "prune", "status": "ok"})
        safe_exit(0)

    # Health check for Ollama before DB mutations
    vlog(args, f"checking Ollama reachability for model {args.model}...")
    client = OllamaClient(embed_model=args.model)
    rec = client.embed_batch(["ping"])
    if rec.error is not None:
        print(f"Error: Ollama unreachable or returned error: {rec.error}", file=sys.stderr)
        run_completed.add(1, {"mode": mode, "status": "failed"})
        safe_exit(1)

    had_errors = False

    try:
        if mode == "full":
            vlog(args, "rebuilding vector tables for --full (staging via pending tables)...")
            conn = sqlite3.connect(target_db)
            cur = conn.cursor()
            cur.execute("DROP TABLE IF EXISTS article_embeddings_pending")
            cur.execute("DROP TABLE IF EXISTS question_embeddings_pending")
            cur.execute("CREATE TABLE article_embeddings_pending (uuid TEXT NOT NULL, lang TEXT NOT NULL, embedding BLOB NOT NULL, PRIMARY KEY (uuid, lang))")
            cur.execute("CREATE TABLE question_embeddings_pending (uuid TEXT NOT NULL, lang TEXT NOT NULL, embedding BLOB NOT NULL, PRIMARY KEY (uuid, lang))")
            conn.commit()
            conn.close()

            missing_articles = kb_db.list_all_article_keys(target_db)
            missing_questions = kb_db.list_all_question_keys(target_db)

            vlog(args, f"full rebuild: {len(missing_articles)} articles, {len(missing_questions)} questions")

            tot_items = len(missing_articles) + len(missing_questions)
            processed = 0
            progress_gauge.set(0.0, {"mode": mode})
            write_progress(0, tot_items)

            def prog_ctx(proc_count, uuid):
                if proc_count % 100 == 0:
                    print(f"Processed {proc_count}/{tot_items} items...", file=sys.stderr)
                    if tot_items > 0:
                        progress_gauge.set(proc_count / tot_items, {"mode": mode})
                    write_progress(proc_count, tot_items, uuid)

            errs, proc = _reindex_batch(target_db, missing_articles, True, client, args, prog_ctx, ollama_errors, pending=True)
            if errs > 0: had_errors = True
            processed += proc

            errs, proc = _reindex_batch(target_db, missing_questions, False, client, args, prog_ctx, ollama_errors, pending=True)
            if errs > 0: had_errors = True
            processed += proc

            # Swap pending tables into live vec0 tables in one transaction
            vlog(args, "swapping pending embeddings into vec0 tables...")
            conn = sqlite3.connect(target_db)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                # Articles swap
                cur.execute("DROP TABLE IF EXISTS article_embeddings")
                cur.execute("DROP TABLE IF EXISTS article_embedding_index")
                cur.execute("CREATE TABLE article_embedding_index (uuid TEXT NOT NULL, lang TEXT NOT NULL, vec_rowid INTEGER NOT NULL UNIQUE, PRIMARY KEY (uuid, lang))")
                cur.execute(f"CREATE VIRTUAL TABLE article_embeddings USING vec0(embedding float[{EMBED_DIMENSIONS}] distance_metric=cosine)")
                article_pending_rows = cur.execute(
                    "SELECT uuid, lang, embedding FROM article_embeddings_pending"
                ).fetchall()
                for uuid, lang, blob in article_pending_rows:
                    cur.execute("INSERT INTO article_embeddings (embedding) VALUES (?)", (sqlite3.Binary(blob),))
                    vrow = cur.lastrowid
                    cur.execute("INSERT INTO article_embedding_index (uuid, lang, vec_rowid) VALUES (?, ?, ?)", (uuid, lang, vrow))
                cur.execute("DROP TABLE IF EXISTS article_embeddings_pending")

                # Questions swap
                cur.execute("DROP TABLE IF EXISTS question_embeddings")
                cur.execute("DROP TABLE IF EXISTS question_embedding_index")
                cur.execute("CREATE TABLE question_embedding_index (uuid TEXT NOT NULL, lang TEXT NOT NULL, vec_rowid INTEGER NOT NULL UNIQUE, PRIMARY KEY (uuid, lang))")
                cur.execute(f"CREATE VIRTUAL TABLE question_embeddings USING vec0(embedding float[{EMBED_DIMENSIONS}] distance_metric=cosine)")
                question_pending_rows = cur.execute(
                    "SELECT uuid, lang, embedding FROM question_embeddings_pending"
                ).fetchall()
                for uuid, lang, blob in question_pending_rows:
                    cur.execute("INSERT INTO question_embeddings (embedding) VALUES (?)", (sqlite3.Binary(blob),))
                    vrow = cur.lastrowid
                    cur.execute("INSERT INTO question_embedding_index (uuid, lang, vec_rowid) VALUES (?, ?, ?)", (uuid, lang, vrow))
                cur.execute("DROP TABLE IF EXISTS question_embeddings_pending")

                cur.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_model', ?)", (args.model,))
                cur.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_dimensions', ?)", (str(EMBED_DIMENSIONS),))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

            kb_db.prune_orphan_embeddings(target_db)

        elif mode == "missing":
            _ensure_schema(target_db)
            missing_articles = kb_db.list_missing_article_keys(target_db)
            missing_questions = kb_db.list_missing_question_keys(target_db)

            tot_items = len(missing_articles) + len(missing_questions)
            vlog(args, f"missing reindex: {len(missing_articles)} articles, {len(missing_questions)} questions")
            processed = 0
            progress_gauge.set(0.0, {"mode": mode})
            write_progress(0, tot_items)

            def prog_ctx(proc_count, uuid):
                if proc_count % 100 == 0:
                    print(f"Processed {proc_count}/{tot_items} items...", file=sys.stderr)
                    if tot_items > 0:
                        progress_gauge.set(proc_count / tot_items, {"mode": mode})
                    write_progress(proc_count, tot_items, uuid)

            errs, proc = _reindex_batch(target_db, missing_articles, True, client, args, prog_ctx, ollama_errors)
            if errs > 0: had_errors = True
            processed += proc

            errs, proc = _reindex_batch(target_db, missing_questions, False, client, args, prog_ctx, ollama_errors)
            if errs > 0: had_errors = True
            processed += proc

            conn = sqlite3.connect(target_db)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            cur = conn.cursor()
            cur.executescript(kb_db.SCHEMA)
            cur.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_model', ?)", (args.model,))
            cur.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('embed_dimensions', ?)", (str(EMBED_DIMENSIONS),))
            conn.commit()
            conn.close()

        elif mode == "domain":
            _ensure_schema(target_db)
            conn = sqlite3.connect(target_db)
            cur = conn.cursor()
            cur.execute("""
                SELECT a.uuid, at.lang
                FROM articles a
                JOIN domains d ON d.id = a.domain_id
                JOIN article_translations at ON at.article_id = a.id
                WHERE d.slug = ? AND (? IS NULL OR at.lang = ?)
            """, (args.domain, args.lang, args.lang))
            domain_articles = cur.fetchall()

            cur.execute("""
                SELECT q.uuid, qt.lang
                FROM questions q
                JOIN domains d ON d.id = q.domain_id
                JOIN question_translations qt ON qt.question_id = q.id
                WHERE d.slug = ? AND (? IS NULL OR qt.lang = ?)
            """, (args.domain, args.lang, args.lang))
            domain_questions = cur.fetchall()
            conn.close()

            tot_items = len(domain_articles) + len(domain_questions)
            vlog(args, f"domain {args.domain} reindex: {len(domain_articles)} articles, {len(domain_questions)} questions")
            processed = 0
            progress_gauge.set(0.0, {"mode": mode})
            write_progress(0, tot_items)

            def prog_ctx(proc_count, uuid):
                if proc_count % 100 == 0:
                    print(f"Processed {proc_count}/{tot_items} items...", file=sys.stderr)
                    if tot_items > 0:
                        progress_gauge.set(proc_count / tot_items, {"mode": mode})
                    write_progress(proc_count, tot_items, uuid)

            errs, proc = _reindex_batch(target_db, domain_articles, True, client, args, prog_ctx, ollama_errors)
            if errs > 0: had_errors = True
            processed += proc

            errs, proc = _reindex_batch(target_db, domain_questions, False, client, args, prog_ctx, ollama_errors)
            if errs > 0: had_errors = True
            processed += proc

    except KeyboardInterrupt:
        print("\nInterrupted by user (Ctrl+C). Exiting...", file=sys.stderr)
        run_completed.add(1, {"mode": mode, "status": "aborted"})
        safe_exit(130)

    if had_errors:
        print("Reindexing finished with some errors.", file=sys.stderr)
        duration = time.time() - run_start_time
        duration_hist.record(duration, {"mode": mode})
        run_completed.add(1, {"mode": mode, "status": "failed"})
        safe_exit(1)
    else:
        print("Reindexing completed successfully.")
        print("=== Reindex Completed Successfully ===")
        write_progress(tot_items, tot_items)
        duration = time.time() - run_start_time
        duration_hist.record(duration, {"mode": mode})
        run_completed.add(1, {"mode": mode, "status": "ok"})
        last_success.set(time.time(), {"mode": mode})
        progress_gauge.set(1.0, {"mode": mode})
        try:
            counts = kb_db.count_missing_embeddings(target_db)
            art_total = counts["articles_total"]
            art_missing = counts["articles_missing"]
            q_total = counts["questions_total"]
            q_missing = counts["questions_missing"]
            if art_total > 0:
                coverage_gauge.set((art_total - art_missing) / art_total, {"kind": "article"})
            if q_total > 0:
                coverage_gauge.set((q_total - q_missing) / q_total, {"kind": "question"})
        except Exception:
            pass
        safe_exit(0)

if __name__ == "__main__":
    main()
