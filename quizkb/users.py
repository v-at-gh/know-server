"""Shared user settings module.

Single source of truth for user profile, generation parameters, favorites,
and effective-parameter resolution. Consumed by web, orchestrator, and CLI
(potentially) to avoid drift between auth modules and endpoint handlers.

Concepts:
  - `generation_params`: global per-user dict, currently flat
    ({articles_per_domain, questions_types}). S2 extends with `per_domain`
    and `per_lang` sub-blocks. Backward compatible: old params without
    extensions still work.
  - `favorites`: subset of active_domains. Auto-pruned on settings update
    if favorites reference inactive/nonexistent domains. Returned in
    `pruned_favorites` in the update response.
  - Effective params resolution: 5-tier merge, later wins:
    defaults → user global → per_domain[domain] → per_lang[lang] → request_override
    `request_override` is for explicit CLI/UI per-invocation overrides only;
    not for auto-derived user settings.

Design:
  - Users DB path resolved from `get_settings().state_dir / "users.db"`.
    Tests monkeypatch this module's `USERS_DB_PATH` attribute directly.
  - Schema ensured via `ensure_schema()`; safe to call from any startup
    or lazy-first-use. Fixes the orchestrator-first-startup race where
    web.auth.init_db hasn't run yet.
  - All write paths atomic (single transaction).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from quizkb.config import QUESTIONS_PER_ARTICLE
from quizkb.settings import get_settings

log = logging.getLogger(__name__)

DEFAULT_GENERATION_PARAMS = {
    "articles_per_domain": 10,
    "questions_types": {"select": 5, "construct": 0},
}

# Module-level path — derived from settings at import time.
# Tests monkeypatch this module attribute (matching orchestrator.tasks.DB_PATH pattern).
USERS_DB_PATH = get_settings().state_dir / "users.db"


@contextmanager
def _transaction(conn: sqlite3.Connection):
    """Commit successful database work, roll back failures, and always close."""
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _users_db_path() -> Path:
    return get_settings().state_dir / "users.db"


def _get_conn() -> sqlite3.Connection:
    USERS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(USERS_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """PRAGMA-based column check. Avoids broad OperationalError suppression."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    # return False  # Sabotage test (PRAGMA check removed)
    return any(r["name"] == column for r in rows)


def ensure_schema() -> None:
    """Create users.db schema if missing; add new columns idempotently.

    Called from web.app lifespan, orchestrator.app lifespan, and
    create_user/create_token scripts. Safe to call multiple times.
    """
    with _transaction(_get_conn()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                active_domains TEXT NOT NULL,
                active_langs TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_tokens (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        # Column migrations — PRAGMA check, not bare except
        if not _column_exists(conn, "users", "generation_params"):
            conn.execute("ALTER TABLE users ADD COLUMN generation_params TEXT DEFAULT '{}'")
        if not _column_exists(conn, "users", "favorites"):
            conn.execute("ALTER TABLE users ADD COLUMN favorites TEXT DEFAULT '[]'")
        if not _column_exists(conn, "users", "webhook_url"):
            conn.execute("ALTER TABLE users ADD COLUMN webhook_url TEXT")
        if not _column_exists(conn, "users", "display_coverage"):
            conn.execute("ALTER TABLE users ADD COLUMN display_coverage TEXT DEFAULT NULL")
        if not _column_exists(conn, "api_tokens", "expires_at"):
            conn.execute("ALTER TABLE api_tokens ADD COLUMN expires_at INTEGER")
        conn.commit()


def _load_generation_params_raw(user_id: int) -> dict:
    """Raw stored JSON from users.db, no default merging."""
    with _transaction(_get_conn()) as conn:
        row = conn.execute(
            "SELECT generation_params FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not row or not row["generation_params"]:
        return {}
    try:
        return json.loads(row["generation_params"])
    except Exception:
        log.warning("invalid_generation_params_json", extra={"user_id": user_id})
        return {}


def _load_active_domains(user_id: int) -> list[str]:
    with _transaction(_get_conn()) as conn:
        row = conn.execute(
            "SELECT active_domains FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not row:
        return []
    try:
        return json.loads(row["active_domains"])
    except Exception:
        return []


def _load_active_langs(user_id: int) -> list[str]:
    with _transaction(_get_conn()) as conn:
        row = conn.execute(
            "SELECT active_langs FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not row:
        return []
    try:
        return json.loads(row["active_langs"])
    except Exception:
        return []


def _load_favorites(user_id: int) -> list[str]:
    with _transaction(_get_conn()) as conn:
        if not _column_exists(conn, "users", "favorites"):
            return []
        row = conn.execute(
            "SELECT favorites FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not row or not row["favorites"]:
        return []
    try:
        return json.loads(row["favorites"])
    except Exception:
        return []


def get_username(user_id: int) -> str | None:
    """Return username for user_id, or None if not found. Used by orchestrator
    create_task to derive authoritative identity from verified token."""
    with _transaction(_get_conn()) as conn:
        row = conn.execute(
            "SELECT username FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return row["username"] if row else None


def get_user_settings(user_id: int) -> dict:
    """Return full user settings, merged with defaults.

    Shape:
        {
          "profile": {"active_domains": [...], "active_langs": [...]},
          "generation_params": {...},  # merged with defaults
          "favorites": [...],
        }
    """
    raw_params = _load_generation_params_raw(user_id)
    merged_params = _merge_with_defaults(raw_params)
    return {
        "profile": {
            "active_domains": _load_active_domains(user_id),
            "active_langs": _load_active_langs(user_id),
        },
        "generation_params": merged_params,
        "favorites": _load_favorites(user_id),
    }


def _merge_with_defaults(raw: dict) -> dict:
    """Merge stored user params over defaults. Preserves unknown top-level
    keys (e.g. `per_domain`, `per_lang`) from stored — forward compatible."""
    result = deepcopy(DEFAULT_GENERATION_PARAMS)
    # articles_per_domain
    if "articles_per_domain" in raw:
        result["articles_per_domain"] = raw["articles_per_domain"]
    # questions_types (deep merge select/construct)
    if "questions_types" in raw and isinstance(raw["questions_types"], dict):
        result["questions_types"] = {
            **result["questions_types"],
            **raw["questions_types"],
        }
    # Forward-compatible: pass through per_domain, per_lang if present
    for key in ("per_domain", "per_lang"):
        if key in raw:
            result[key] = raw[key]
    return result


def validate_settings_patch(patch: dict, *, current_active_domains: list[str] | None = None) -> None:
    """Validate a settings update patch. Raises ValueError on invalid input.

    Does not mutate. Called before persistence. Validates:
    - articles_per_domain: int 1..30
    - questions_types: dict with select/construct non-negative ints, sum 1..20
    - per_domain: dict slug -> {articles_per_domain?, questions_types?}
    - per_lang: dict lang -> {translate_domains?: "*" | list[str]}
    - favorites: list[str], subset of resulting active_domains
    """
    if "generation_params" in patch:
        gp = patch["generation_params"]
        if not isinstance(gp, dict):
            raise ValueError("generation_params must be a dict")
        if "articles_per_domain" in gp:
            apd = gp["articles_per_domain"]
            if not isinstance(apd, int) or not (1 <= apd <= 30):
                raise ValueError("articles_per_domain must be integer 1..30")
        if "questions_types" in gp:
            qt = gp["questions_types"]
            if not isinstance(qt, dict):
                raise ValueError("questions_types must be a dict")
            allowed = {"select", "construct"}
            merged = {**DEFAULT_GENERATION_PARAMS["questions_types"], **qt}
            for k, v in merged.items():
                if k not in allowed:
                    raise ValueError(f"invalid questions_types key: {k}")
                if not isinstance(v, int) or v < 0:
                    raise ValueError(f"questions_types[{k}] must be non-negative int")
            if not (1 <= sum(merged.values()) <= 20):
                raise ValueError("questions_types sum must be 1..20")
        if "per_domain" in gp:
            pd = gp["per_domain"]
            if not isinstance(pd, dict):
                raise ValueError("per_domain must be a dict")
            for domain, override in pd.items():
                if not isinstance(override, dict):
                    raise ValueError(f"per_domain[{domain}] must be a dict")
                # Reuse top-level validators on the override sub-payload
                validate_settings_patch({"generation_params": override})
        if "per_lang" in gp:
            pl = gp["per_lang"]
            if not isinstance(pl, dict):
                raise ValueError("per_lang must be a dict")
            for lang, override in pl.items():
                if not isinstance(override, dict):
                    raise ValueError(f"per_lang[{lang}] must be a dict")
                td = override.get("translate_domains")
                if td is not None:
                    if td == "*":
                        pass  # wildcard OK
                    elif isinstance(td, list) and all(isinstance(x, str) for x in td):
                        pass  # list of slugs OK
                    else:
                        raise ValueError(
                            f"per_lang[{lang}].translate_domains must be '*' or list[str]"
                        )
    if "favorites" in patch:
        fav = patch["favorites"]
        if not isinstance(fav, list) or not all(isinstance(x, str) for x in fav):
            raise ValueError("favorites must be list[str]")
        # If patch also carries active_domains, use that; else caller passes
        # current_active_domains
        if "active_domains" in patch:
            check_against = patch["active_domains"]
        elif current_active_domains is not None:
            check_against = current_active_domains
        else:
            check_against = None
        if check_against is not None:
            invalid = [f for f in fav if f not in check_against]
            if invalid:
                raise ValueError(f"favorites not in active_domains: {invalid}")


def update_user_settings(user_id: int, patch: dict) -> dict:
    """Apply a partial settings update atomically.

    Patch shape (all fields optional):
        {
          "active_domains": [str],
          "active_langs": [str],
          "generation_params": {articles_per_domain?, questions_types?,
                                per_domain?, per_lang?},
          "favorites": [str],
        }

    Returns full settings after update, plus `pruned_favorites` list
    (subset of submitted favorites removed because they're not in the
    resulting active_domains).

    Behavior:
    - favorites auto-pruned against resulting active_domains (silent).
    - generation_params deep-merged with existing (partial update OK).
    - active_domains/active_langs replace (not merge) — explicit set.
    - Raises ValueError on validation failure before any write.
    """
    current = get_user_settings(user_id)
    # Determine resulting active_domains first (needed for favorites validation)
    resulting_active = (
        patch.get("active_domains") if "active_domains" in patch
        else current["profile"]["active_domains"]
    )
    resulting_langs = (
        patch.get("active_langs") if "active_langs" in patch
        else current["profile"]["active_langs"]
    )

    validate_settings_patch(
        patch,
        current_active_domains=resulting_active,
    )

    # Merge generation_params
    merged_gp = deepcopy(current["generation_params"])
    if "generation_params" in patch:
        incoming = patch["generation_params"]
        for key in ("articles_per_domain", "questions_types"):
            if key in incoming:
                if key == "questions_types" and isinstance(incoming[key], dict):
                    merged_gp["questions_types"] = {
                        **merged_gp.get("questions_types", {}),
                        **incoming[key],
                    }
                else:
                    merged_gp[key] = incoming[key]
        for key in ("per_domain", "per_lang"):
            if key in incoming:
                merged_gp[key] = incoming[key]

    # Favorites: intersect with resulting active_domains
    requested_favorites = (
        patch["favorites"] if "favorites" in patch else current["favorites"]
    )
    pruned_favorites = [f for f in requested_favorites if f not in resulting_active]
    resulting_favorites = [f for f in requested_favorites if f in resulting_active]

    # Single transaction
    with _transaction(_get_conn()) as conn:
        conn.execute(
            "UPDATE users SET active_domains=?, active_langs=?, generation_params=?, favorites=? WHERE id=?",
            (
                json.dumps(resulting_active),
                json.dumps(resulting_langs),
                json.dumps(merged_gp),
                json.dumps(resulting_favorites),
                user_id,
            ),
        )
        conn.commit()

    log.info(
        "user_settings_updated",
        extra={
            "user_id": user_id,
            "pruned_favorites": pruned_favorites,
            "gp_keys": list(merged_gp.keys()),
        },
    )

    result = get_user_settings(user_id)
    result["pruned_favorites"] = pruned_favorites
    return result


def resolve_effective_generation_params(
    user_id: int,
    *,
    domain: str | None = None,
    lang: str | None = None,
    request_override: dict | None = None,
) -> dict:
    """5-tier merge for task creation / planning.

    Order (later wins):
      1. DEFAULT_GENERATION_PARAMS
      2. user global generation_params
      3. per_domain[domain] (if domain provided)
      4. per_lang[lang] (if lang provided)
      5. request_override (explicit CLI/UI override)

    Returns full merged dict with articles_per_domain and questions_types
    (sub-block per_domain/per_lang NOT included in result — they were applied).
    """
    base = deepcopy(DEFAULT_GENERATION_PARAMS)
    stored = _merge_with_defaults(_load_generation_params_raw(user_id))

    # Apply global (stored already merged with defaults)
    base["articles_per_domain"] = stored["articles_per_domain"]
    base["questions_types"] = dict(stored["questions_types"])

    # Apply per_domain[domain]
    if domain and "per_domain" in stored:
        dom_over = stored["per_domain"].get(domain, {})
        if "articles_per_domain" in dom_over:
            base["articles_per_domain"] = dom_over["articles_per_domain"]
        if "questions_types" in dom_over:
            base["questions_types"] = {
                **base["questions_types"],
                **dom_over["questions_types"],
            }

    # Apply per_lang[lang] — only affects translate_domains semantics,
    # no direct params for now (translate_domains is a filter, not a param)
    # Future per_lang params would slot here.

    # Apply request override
    if request_override:
        if "articles_per_domain" in request_override:
            base["articles_per_domain"] = request_override["articles_per_domain"]
        if "questions_types" in request_override and isinstance(request_override["questions_types"], dict):
            base["questions_types"] = {
                **base["questions_types"],
                **request_override["questions_types"],
            }

    return base


def get_effective_question_count(user_id: int, domain: str | None = None) -> int:
    """Convenience: sum of effective questions_types (used by plan endpoints
    and check_pack for user-facing status)."""
    gp = resolve_effective_generation_params(user_id, domain=domain)
    return sum(gp["questions_types"].values()) or QUESTIONS_PER_ARTICLE


def get_effective_questions_types_batch(
    user_id: int,
    domains: list[str],
) -> dict[str, dict]:
    """Resolve effective questions_types for multiple domains in one DB read.

    Returns {domain_slug: {"select": N, "construct": N}}.
    Domains with no per-domain override inherit global questions_types.
    Avoids N+1 DB queries when iterating domain lists (e.g. api_get_domains).

    Stored params are trusted (validated at write time via
    update_user_settings); malformed per_domain values silently ignored
    (inherit global).
    """
    stored = _merge_with_defaults(_load_generation_params_raw(user_id))
    global_qt = stored["questions_types"]
    per_domain = stored.get("per_domain", {})

    result: dict[str, dict] = {}
    for domain in domains:
        domain_qt = dict(global_qt)
        dom_override = per_domain.get(domain, {})
        if "questions_types" in dom_override and isinstance(dom_override["questions_types"], dict):
            domain_qt.update(dom_override["questions_types"])
        result[domain] = domain_qt
    return result
