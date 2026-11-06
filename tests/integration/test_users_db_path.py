"""Path resolution: both auth modules see same users.db, honoring settings."""

# import pytest
# from pathlib import Path


def test_web_and_orchestrator_share_path(monkeypatch, tmp_path):
    from quizkb import users as kb_users
    monkeypatch.setattr(kb_users, "USERS_DB_PATH", tmp_path / "users.db")

    from web import auth as web_auth
    from orchestrator import auth as orch_auth

    with web_auth.get_db() as conn:
        conn.execute("SELECT 1")
    with orch_auth.get_users_db() as conn:
        conn.execute("SELECT 1")

    assert (tmp_path / "users.db").exists()

def test_settings_override_honored(monkeypatch, tmp_path):
    from quizkb.settings import reset_settings_cache
    from quizkb import users as kb_users
    monkeypatch.setenv("KNOW_STATE_DIR", str(tmp_path))
    reset_settings_cache()
    assert kb_users._users_db_path() == tmp_path / "users.db"