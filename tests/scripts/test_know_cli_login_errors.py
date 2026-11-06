import pytest
from unittest.mock import MagicMock


def _resp(status, body="", content_type="application/json"):
    r = MagicMock()
    r.status_code = status
    r.text = body
    r.headers = {"content-type": content_type}
    return r


def test_login_401_suggests_bootstrap_and_reset(monkeypatch, capsys):
    """401 → message suggests both bootstrap and reset-password."""
    from scripts import know_cli
    monkeypatch.setattr(know_cli.requests, "post",
                        lambda *a, **kw: _resp(401, '{"detail":"Invalid"}'))
    monkeypatch.setattr(know_cli, "load_config", lambda: {
        "admin_url": "http://x", "orch_url": "http://x",
        "public_url": "http://x", "token": "",
    })
    monkeypatch.setattr("sys.argv",
        ["know", "auth", "login", "--username", "admin", "--password", "pw"])
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "invalid username or password" in err.lower()
    assert "know users bootstrap --username admin" in err
    assert "know users reset-password --username admin" in err


def test_login_rejects_user_abbrev(monkeypatch):
    """--user rejected (allow_abbrev=False on auth login parser)."""
    from scripts import know_cli
    monkeypatch.setattr(know_cli, "load_config", lambda: {
        "admin_url": "http://x", "orch_url": "http://x",
        "public_url": "http://x", "token": "",
    })
    monkeypatch.setattr("sys.argv",
        ["know", "auth", "login", "--user", "admin", "--password", "pw"])
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 2  # argparse error
