"""Unit tests for web/auth.py lifecycle functions."""

import pytest


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    from quizkb import users as kb_users
    monkeypatch.setattr(kb_users, "USERS_DB_PATH", tmp_path / "users.db")
    if hasattr(kb_users, "_users_db_path"):
        monkeypatch.setattr(kb_users, "_users_db_path", lambda: tmp_path / "users.db")
    kb_users.ensure_schema()
    yield


def test_create_then_get_by_username(isolated_db):
    from web import auth
    assert auth.create_user("alice", "password123", ["A"], ["en"])
    u = auth.get_user_by_username("alice")
    assert u is not None
    assert u.username == "alice"
    assert u.active_domains == ["A"]


def test_list_users_empty(isolated_db):
    from web import auth
    assert auth.list_users() == []


def test_list_users_multiple(isolated_db):
    from web import auth
    auth.create_user("bob", "password123", [], [])
    auth.create_user("alice", "password123", [], [])
    users = auth.list_users()
    assert len(users) == 2
    assert [u.username for u in users] == ["alice", "bob"]


def test_delete_user_removes_tokens(isolated_db):
    """F1 regression: PRAGMA foreign_keys is OFF; explicit token delete required."""
    from web import auth
    auth.create_user("alice", "password123", [], [])
    u = auth.get_user_by_username("alice")
    token = auth.create_api_token(u.id)
    assert token

    assert auth.delete_user(u.id) is True
    assert auth.get_user_by_id(u.id) is None
    assert not any(t["token"] == token for t in auth.list_user_tokens(u.id))


def test_delete_user_nonexistent(isolated_db):
    from web import auth
    assert auth.delete_user(9999) is False


def test_update_user_password(isolated_db):
    from web import auth
    auth.create_user("alice", "oldpassword", [], [])
    u = auth.get_user_by_username("alice")
    assert auth.authenticate("alice", "oldpassword") is not None
    assert auth.update_user_password(u.id, "newpassword") is True
    assert auth.authenticate("alice", "oldpassword") is None
    assert auth.authenticate("alice", "newpassword") is not None


def test_update_password_too_short(isolated_db):
    from web import auth
    auth.create_user("alice", "password123", [], [])
    u = auth.get_user_by_username("alice")
    with pytest.raises(ValueError, match="at least 8"):
        auth.update_user_password(u.id, "short")


def test_list_tokens_and_revoke(isolated_db):
    from web import auth
    auth.create_user("alice", "password123", [], [])
    u = auth.get_user_by_username("alice")
    t1 = auth.create_api_token(u.id)
    t2 = auth.create_api_token(u.id)
    tokens = auth.list_user_tokens(u.id)
    assert len(tokens) == 2
    assert {t["token"] for t in tokens} == {t1, t2}

    assert auth.revoke_token(t1) is True
    tokens = auth.list_user_tokens(u.id)
    assert len(tokens) == 1
    assert tokens[0]["token"] == t2


def test_revoke_nonexistent_token(isolated_db):
    from web import auth
    assert auth.revoke_token("know_nonexistent") is False
