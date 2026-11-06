"""Authentication utilities for Orchestrator microservice."""
from __future__ import annotations

import sqlite3
from fastapi import Header, HTTPException
from quizkb import users as kb_users

_ensured_paths: set[str] = set()


def get_users_db():
    global _ensured_paths
    path = kb_users.USERS_DB_PATH
    path_str = str(path.resolve())
    if path_str not in _ensured_paths:
        kb_users.ensure_schema()
        _ensured_paths.add(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def verify_token(authorization: str | None = Header(None)) -> str:
    """Validates Bearer token against api_tokens table in users.db."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ")[1]
    with get_users_db() as conn:
        row = conn.execute("SELECT user_id FROM api_tokens WHERE token = ?", (token,)).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid API token")
        return str(row["user_id"])
