import pytest


@pytest.fixture
def isolated_users_db(tmp_path, monkeypatch):
    users_db = tmp_path / "users.db"
    monkeypatch.setattr("quizkb.users.USERS_DB_PATH", users_db)
    if hasattr(__import__("quizkb.users", fromlist=["x"]), "_users_db_path"):
        monkeypatch.setattr("quizkb.users._users_db_path", lambda: users_db)
    import web.auth
    monkeypatch.setattr(web.auth, "USERS_DB_PATH", users_db)
    web.auth.init_db()
    return users_db


def test_reset_password_updates_hash(isolated_users_db, monkeypatch, capsys):
    """Reset changes password; old password no longer works."""
    from web import auth
    auth.create_user("admin", "oldpass123", [], [])

    from scripts import know_cli
    monkeypatch.setattr("sys.argv",
        ["know", "users", "reset-password",
         "--username", "admin", "--password", "newpass456"])
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Password for 'admin' reset" in out

    assert auth.authenticate("admin", "oldpass123") is None
    assert auth.authenticate("admin", "newpass456") is not None


def test_reset_password_revokes_tokens(isolated_users_db, monkeypatch, capsys):
    """Reset revokes all API tokens for the user."""
    from web import auth
    auth.create_user("admin", "oldpass123", [], [])
    user = auth.get_user_by_username("admin")
    auth.create_api_token(user.id)
    auth.create_api_token(user.id)
    assert len(auth.list_user_tokens(user.id)) == 2

    from scripts import know_cli
    monkeypatch.setattr("sys.argv",
        ["know", "users", "reset-password",
         "--username", "admin", "--password", "newpass456"])
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 0

    assert len(auth.list_user_tokens(user.id)) == 0
    out = capsys.readouterr().out
    assert "Revoked 2 API token" in out


def test_reset_password_unknown_user(isolated_users_db, monkeypatch, capsys):
    """Unknown username → error, exit 1."""
    from scripts import know_cli
    monkeypatch.setattr("sys.argv",
        ["know", "users", "reset-password",
         "--username", "nobody", "--password", "newpass456"])
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_reset_password_refuses_service_account(isolated_users_db, monkeypatch, capsys):
    """sys_* accounts refused."""
    from web import auth
    auth.create_user("sys_orchestrator", "syspass123", [], [])

    from scripts import know_cli
    monkeypatch.setattr("sys.argv",
        ["know", "users", "reset-password",
         "--username", "sys_orchestrator", "--password", "newpass456"])
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "service account" in err


def test_reset_password_short_password(isolated_users_db, monkeypatch, capsys):
    """< 8 chars → error, exit 2, no change."""
    from web import auth
    auth.create_user("admin", "oldpass123", [], [])

    from scripts import know_cli
    monkeypatch.setattr("sys.argv",
        ["know", "users", "reset-password",
         "--username", "admin", "--password", "short"])
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 2
    assert auth.authenticate("admin", "oldpass123") is not None  # unchanged
