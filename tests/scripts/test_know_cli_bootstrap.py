import pytest


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Isolate users.db and app.db from real state/."""
    users_db = tmp_path / "users.db"
    app_db = tmp_path / "app.db"
    monkeypatch.setattr("quizkb.users.USERS_DB_PATH", users_db)
    if hasattr(__import__("quizkb.users", fromlist=["x"]), "_users_db_path"):
        monkeypatch.setattr("quizkb.users._users_db_path", lambda: users_db)
    import web.auth
    monkeypatch.setattr(web.auth, "USERS_DB_PATH", users_db)
    import web.db
    monkeypatch.setattr(web.db, "APP_DB_PATH", app_db)
    from web.db import init_app_db
    init_app_db()
    return {"users_db": users_db, "app_db": app_db}


def test_bootstrap_creates_first_user(isolated_state, monkeypatch, capsys):
    """Empty users table → creates user with all domains/langs."""
    from scripts import know_cli
    monkeypatch.setattr("sys.argv",
        ["know", "users", "bootstrap",
         "--username", "admin", "--password", "adminpass123"])
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 0

    out = capsys.readouterr()
    assert "Created first user 'admin'" in out.out
    assert "know auth login --username admin" in out.out

    from web import auth
    user = auth.authenticate("admin", "adminpass123")
    assert user is not None
    assert len(user.active_domains) > 0
    assert len(user.active_langs) > 0


def test_bootstrap_refuses_when_users_exist(isolated_state, monkeypatch, capsys):
    """Non-service users block bootstrap."""
    from web import auth
    auth.init_db()
    auth.create_user("existing", "existingpw123", [], [])  # not sys_*
    auth.create_user("sys_orchestrator", "syspass123", [], [])

    from scripts import know_cli
    monkeypatch.setattr("sys.argv",
        ["know", "users", "bootstrap",
         "--username", "admin", "--password", "adminpass123"])
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 1

    err = capsys.readouterr().err
    assert "already contains 1 non-service user" in err
    assert "existing" in err
    assert "reset-password" in err  # new hint
    # Existing user NOT touched
    assert auth.authenticate("existing", "existingpw123") is not None
    assert auth.authenticate("admin", "adminpass123") is None


def test_bootstrap_ignores_service_accounts(isolated_state, monkeypatch, capsys):
    """sys_* users don't block bootstrap."""
    from web import auth
    auth.init_db()
    auth.create_user("sys_orchestrator", "sysrandompass123", [], [])
    auth.create_user("sys_worker", "sysrandompass456", [], [])

    from scripts import know_cli
    monkeypatch.setattr("sys.argv",
        ["know", "users", "bootstrap",
         "--username", "admin", "--password", "adminpass123"])
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "Created first user 'admin'" in out

    # Verify
    assert auth.authenticate("admin", "adminpass123") is not None
    assert auth.authenticate("sys_orchestrator", "sysrandompass123") is not None


def test_bootstrap_rejects_short_password(isolated_state, monkeypatch, capsys):
    """Password < 8 chars → error, no user created."""
    from scripts import know_cli
    monkeypatch.setattr("sys.argv",
        ["know", "users", "bootstrap",
         "--username", "admin", "--password", "short"])
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 2
    assert "at least 8 characters" in capsys.readouterr().err


def test_bootstrap_force_wipes_and_creates(isolated_state, monkeypatch, capsys):
    """--force deletes non-service users, creates fresh admin, preserves sys_*."""
    from web import auth
    auth.init_db()
    auth.create_user("old1", "oldpass1234", [], [])
    auth.create_user("old2", "oldpass1234", [], [])
    auth.create_user("sys_orchestrator", "syspass123", [], [])

    from scripts import know_cli
    monkeypatch.setattr("sys.argv",
        ["know", "users", "bootstrap",
         "--username", "admin", "--password", "adminpass123",
         "--force"])
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 0

    assert auth.authenticate("old1", "oldpass1234") is None
    assert auth.authenticate("old2", "oldpass1234") is None
    assert auth.authenticate("admin", "adminpass123") is not None
    assert auth.authenticate("sys_orchestrator", "syspass123") is not None


def test_bootstrap_force_requires_confirmation_without_password(
    isolated_state, monkeypatch, capsys
):
    """--force without --password requires 'yes' confirmation."""
    from web import auth
    auth.init_db()
    auth.create_user("old", "oldpass1234", [], [])

    from scripts import know_cli
    monkeypatch.setattr("sys.argv",
        ["know", "users", "bootstrap", "--username", "admin", "--force"])
    monkeypatch.setattr("builtins.input", lambda _: "no")
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 1
    assert auth.authenticate("old", "oldpass1234") is not None  # unchanged
