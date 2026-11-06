import pytest
from unittest.mock import MagicMock


@pytest.fixture
def cfg():
    return {
        "admin_url": "http://127.0.0.1:8000",
        "orch_url": "http://127.0.0.1:8002",
        "public_url": "http://127.0.0.1:8001",
        "token": "",
    }


def _make_response(status=200, content_type="application/json", body='{"ok":1}', location=None):
    r = MagicMock()
    r.status_code = status
    r.headers = {"content-type": content_type}
    if location:
        r.headers["location"] = location
    r.text = body
    r.url = "http://127.0.0.1:8000/api/v1/..."  # not used after refactor
    if body and content_type == "application/json":
        import json
        r.json.return_value = json.loads(body)
    else:
        import json
        def _json_side_effect():
            raise json.JSONDecodeError("Expecting value", body or "", 0)
        r.json.side_effect = _json_side_effect
    return r


def test_admin_call_detects_login_redirect(cfg, monkeypatch, capsys):
    """303 to /login raises SystemExit with 'not authenticated' message."""
    from scripts import know_cli
    def fake_get(url, **kw):
        return _make_response(303, location="/login")
    monkeypatch.setattr(know_cli.requests, "get", fake_get)
    with pytest.raises(SystemExit) as exc:
        know_cli._admin_call("GET", "/api/v1/users", cfg)
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "not authenticated" in captured.err
    assert "know auth login" in captured.err


def test_admin_call_detects_unexpected_redirect(cfg, monkeypatch, capsys):
    """303 to non-login location reports unexpected redirect."""
    from scripts import know_cli
    def fake_get(url, **kw):
        return _make_response(303, location="/somewhere")
    monkeypatch.setattr(know_cli.requests, "get", fake_get)
    with pytest.raises(SystemExit) as exc:
        know_cli._admin_call("GET", "/api/v1/users", cfg)
    assert exc.value.code == 1
    assert "unexpected redirect" in capsys.readouterr().err


def test_admin_json_rejects_html_200(cfg, monkeypatch, capsys):
    """200 with text/html body → non-JSON error, exits."""
    from scripts import know_cli
    def fake_get(url, **kw):
        return _make_response(200, content_type="text/html", body="<html>...</html>")
    monkeypatch.setattr(know_cli.requests, "get", fake_get)
    with pytest.raises(SystemExit) as exc:
        know_cli._admin_json("GET", "/api/v1/users", cfg)
    assert exc.value.code == 1
    assert "non-JSON" in capsys.readouterr().err


def test_admin_json_returns_parsed(cfg, monkeypatch):
    """200 with JSON → returns dict."""
    from scripts import know_cli
    def fake_get(url, **kw):
        return _make_response(200, body='{"users": [], "total": 0}')
    monkeypatch.setattr(know_cli.requests, "get", fake_get)
    data = know_cli._admin_json("GET", "/api/v1/users", cfg)
    assert data == {"users": [], "total": 0}


def test_users_list_with_auth_redirect_friendly_error(cfg, monkeypatch, capsys):
    """End-to-end: know users list on unauth admin → friendly error, no traceback."""
    from scripts import know_cli
    def fake_get(url, **kw):
        return _make_response(303, location="/login")
    monkeypatch.setattr(know_cli.requests, "get", fake_get)
    monkeypatch.setattr(know_cli, "load_config", lambda: cfg)
    monkeypatch.setattr("sys.argv", ["know", "users", "list"])
    with pytest.raises(SystemExit) as exc:
        know_cli.main()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "not authenticated" in captured.err
    assert "JSONDecodeError" not in captured.err
    assert "Traceback" not in captured.err
