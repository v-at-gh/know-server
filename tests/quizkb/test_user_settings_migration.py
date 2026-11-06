"""Migration tests: idempotent, PRAGMA-checked, no broad exception suppression."""

import sqlite3
import pytest
from quizkb import users as kb_users


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(kb_users, "USERS_DB_PATH", tmp_path / "users.db")
    yield


def test_ensure_schema_creates_fresh():
    kb_users.ensure_schema()
    with kb_users._get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        token_cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_tokens)")}
    assert {"id", "username", "password_hash", "active_domains", "active_langs",
            "generation_params", "favorites", "webhook_url", "display_coverage"} <= cols
    assert {"token", "user_id", "created_at", "expires_at"} <= token_cols


def test_ensure_schema_idempotent():
    kb_users.ensure_schema()
    kb_users.ensure_schema()  # no exception
    kb_users.ensure_schema()  # no exception


def test_ensure_schema_from_old_baseline():
    """Simulate pre-S2 users.db, then ensure_schema adds columns."""
    USERS_DB_PATH = kb_users.USERS_DB_PATH
    USERS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(USERS_DB_PATH)
    conn.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            active_domains TEXT NOT NULL,
            active_langs TEXT NOT NULL,
            generation_params TEXT DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE TABLE api_tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at INTEGER
        )
    """)
    conn.commit()
    conn.close()

    kb_users.ensure_schema()

    with kb_users._get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        assert "favorites" in cols
        assert "webhook_url" in cols
        assert "display_coverage" in cols
        token_cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_tokens)")}
        assert "expires_at" in token_cols


def test_ensure_schema_creates_display_coverage():
    """display_coverage column exists after ensure_schema."""
    kb_users.ensure_schema()
    with kb_users._get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    assert "display_coverage" in cols


def test_display_coverage_default_null():
    """New users have display_coverage = NULL (unset)."""
    kb_users.ensure_schema()
    from web import auth
    auth.create_user("test", "password123", ["alpha"], ["en"])
    with kb_users._get_conn() as conn:
        row = conn.execute("SELECT display_coverage FROM users WHERE username='test'").fetchone()
    assert row["display_coverage"] is None

