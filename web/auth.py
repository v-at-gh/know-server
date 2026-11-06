"""User authentication and profile storage."""
from __future__ import annotations

import json
import sqlite3
import hashlib
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from quizkb import users as kb_users

# `get_db()` uses fresh path,
# module attribute is snapshot for external readers only
USERS_DB_PATH = kb_users.USERS_DB_PATH


@dataclass
class User:
    id: int
    username: str
    active_domains: list[str]
    active_langs: list[str]


def get_db():
    path = kb_users.USERS_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    kb_users.ensure_schema()


DEFAULT_GENERATION_PARAMS = kb_users.DEFAULT_GENERATION_PARAMS


def get_generation_params(user_id: int) -> dict:
    settings = kb_users.get_user_settings(user_id)
    return {
        "articles_per_domain": settings["generation_params"]["articles_per_domain"],
        "questions_types": dict(settings["generation_params"]["questions_types"]),
    }


def update_generation_params(user_id: int, params: dict) -> dict:
    kb_users.update_user_settings(
        user_id,
        {"generation_params": params},
    )
    return get_generation_params(user_id)


def create_api_token(user_id: int) -> str:
    token = "know_" + secrets.token_urlsafe(32)
    with get_db() as conn:
        conn.execute("INSERT INTO api_tokens (token, user_id, created_at) VALUES (?, ?, ?)", 
                     (token, user_id, int(time.time())))
        conn.commit()
    return token


def hash_password(password: str) -> str:
    """Computes a secure password hash using PBKDF2-HMAC-SHA256.

    Generates a cryptographically secure random 16-byte salt and applies 100,000
    iterations to protect against brute-force and rainbow table attacks.
    """
    salt = secrets.token_bytes(16)
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return f"{salt.hex()}${pwd_hash.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verifies a plain-text password against a stored PBKDF2-HMAC-SHA256 hash.

    Extracts the salt, recomputes the hash using 100,000 iterations, and performs
    a constant-time comparison (`secrets.compare_digest`) to prevent timing attacks.
    """
    try:
        salt_hex, hash_hex = stored_hash.split('$')
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)
        pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        return secrets.compare_digest(pwd_hash, expected_hash)
    except Exception:
        return False


def create_user(username: str, password: str, all_domains: list[str], all_langs: list[str]) -> bool:
    pwd_hash = hash_password(password)
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, active_domains, active_langs) VALUES (?, ?, ?, ?)",
                (username, pwd_hash, json.dumps(all_domains), json.dumps(all_langs))
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def authenticate(username: str, password: str) -> User | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            return None
        if verify_password(password, row["password_hash"]):
            return User(
                id=row["id"],
                username=row["username"],
                active_domains=json.loads(row["active_domains"]),
                active_langs=json.loads(row["active_langs"])
            )
    return None


def get_user_by_id(user_id: int) -> User | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row:
            return User(
                id=row["id"],
                username=row["username"],
                active_domains=json.loads(row["active_domains"]),
                active_langs=json.loads(row["active_langs"])
            )
    return None


def update_user_settings(user_id: int, domains: list[str], langs: list[str]):
    kb_users.update_user_settings(
        user_id,
        {"active_domains": domains, "active_langs": langs},
    )


def get_user_by_username(username: str) -> User | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row:
            return User(
                id=row["id"],
                username=row["username"],
                active_domains=json.loads(row["active_domains"]),
                active_langs=json.loads(row["active_langs"]),
            )
    return None


def list_users() -> list[User]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [
            User(
                id=r["id"],
                username=r["username"],
                active_domains=json.loads(r["active_domains"]),
                active_langs=json.loads(r["active_langs"]),
            )
            for r in rows
        ]


def delete_user(user_id: int) -> bool:
    """Delete user and all associated API tokens in one transaction.

    Cascade is explicit — PRAGMA foreign_keys is OFF in this codebase (F1),
    so ON DELETE CASCADE on api_tokens.user_id was never active.
    """
    with get_db() as conn:
        conn.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


def update_user_password(user_id: int, new_password: str) -> bool:
    if len(new_password) < 8:
        raise ValueError("password must be at least 8 characters")
    pwd_hash = hash_password(new_password)
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (pwd_hash, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def list_user_tokens(user_id: int) -> list[dict]:
    """Return [{token, created_at}] for user_id, newest first."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT token, created_at FROM api_tokens "
            "WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [{"token": r["token"], "created_at": r["created_at"]} for r in rows]


def revoke_token(token: str) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM api_tokens WHERE token = ?", (token,))
        conn.commit()
        return cur.rowcount > 0

