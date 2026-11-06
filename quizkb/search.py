"""Shared search runner. Extracted from web/app.py::api_search.

Both REST handler (`api_search`) and Jinja handler (`search_view`) call this.
Eliminates logic duplication; single source of truth for hybrid search.
"""
from __future__ import annotations

import logging
import sqlite3
import sqlite_vec
import time
from pathlib import Path
from typing import Any

from . import db as kb_db

log = logging.getLogger("quizkb.search")


def run_search(
    db_path: Path,
    query: str,
    lang: str | None,
    search_type: str,
    *,
    active_langs: list[str] | None = None,
    ollama_client: Any | None = None,
    semantic_available: bool = False,
) -> tuple[list[dict], list[dict], bool, int, str | None]:
    """Run hybrid search. Returns (articles, questions, semantic_used, elapsed_ms, effective_lang).

    - `lang` is dropped to None if not in `active_langs` (multi-tenant filter).
    - Empty query returns empty lists immediately.
    - If semantic_available + ollama_client: computes embedding, uses hybrid
      FTS+vec RRF. Otherwise falls back to FTS-only.
    """
    t0 = time.monotonic()
    q_clean = query.strip() if query else ""
    if not q_clean:
        return [], [], False, 0, lang

    effective_lang = lang or None  # normalize "" to None (empty HTML form field means "all")
    if active_langs is not None and effective_lang and effective_lang not in active_langs:
        effective_lang = None

    do_articles = search_type in ("all", "articles")
    do_questions = search_type in ("all", "questions")

    query_vec = None
    semantic_used = False
    if semantic_available and ollama_client is not None:
        rec = ollama_client.embed_batch([q_clean])
        if rec.error is None and rec.embeddings:
            query_vec = sqlite3.Binary(
                sqlite_vec.serialize_float32(rec.embeddings[0])
            )
            semantic_used = True
        else:
            log.warning("search: query embed failed: %s", rec.error)

    articles = (
        kb_db.search_articles_hybrid(db_path, q_clean, query_vec, lang=effective_lang)
        if do_articles else []
    )
    questions = (
        kb_db.search_questions_hybrid(db_path, q_clean, query_vec, lang=effective_lang)
        if do_questions else []
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    return articles, questions, semantic_used, elapsed_ms, effective_lang
