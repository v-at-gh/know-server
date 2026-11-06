"""Race fix: orchestrator can verify_token before admin init_db."""

import pytest
from fastapi.testclient import TestClient


def test_orchestrator_boot_before_admin(monkeypatch, tmp_path):
    """Fresh users.db, orchestrator's verify_token called without admin
    running — must not raise 'no such table: api_tokens'."""
    from quizkb import users as kb_users
    monkeypatch.setattr(kb_users, "USERS_DB_PATH", tmp_path / "users.db")

    from orchestrator.auth import verify_token
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        verify_token("Bearer nonexistent-token")
    assert exc.value.status_code == 401
