"""Verify schema caching in orchestrator.auth via _ensured_paths set."""

import pytest


def test_schema_ensured_called_once(monkeypatch, tmp_path):
    from quizkb import users as kb_users
    monkeypatch.setattr(kb_users, "USERS_DB_PATH", tmp_path / "users.db")
    if hasattr(kb_users, "_users_db_path"):
        monkeypatch.setattr(kb_users, "_users_db_path", lambda: tmp_path / "users.db")

    from orchestrator import auth as orch_auth
    orch_auth._ensured_paths.clear()

    call_count = [0]
    original_ensure = kb_users.ensure_schema
    def counting_ensure():
        call_count[0] += 1
        original_ensure()
    monkeypatch.setattr(kb_users, "ensure_schema", counting_ensure)

    from fastapi import HTTPException
    for _ in range(3):
        with pytest.raises(HTTPException):
            orch_auth.verify_token("Bearer invalid-token")

    assert call_count[0] == 1


def test_schema_ensured_resets_per_process(monkeypatch, tmp_path):
    from quizkb import users as kb_users
    monkeypatch.setattr(kb_users, "USERS_DB_PATH", tmp_path / "users.db")
    if hasattr(kb_users, "_users_db_path"):
        monkeypatch.setattr(kb_users, "_users_db_path", lambda: tmp_path / "users.db")

    from orchestrator import auth as orch_auth

    call_count = [0]
    original_ensure = kb_users.ensure_schema
    def counting_ensure():
        call_count[0] += 1
        original_ensure()
    monkeypatch.setattr(kb_users, "ensure_schema", counting_ensure)

    from fastapi import HTTPException

    orch_auth._ensured_paths.clear()
    with pytest.raises(HTTPException):
        orch_auth.verify_token("Bearer x")
    assert call_count[0] == 1

    # Simulate restart / new db path or cleared set
    orch_auth._ensured_paths.clear()
    with pytest.raises(HTTPException):
        orch_auth.verify_token("Bearer y")
    assert call_count[0] == 2
