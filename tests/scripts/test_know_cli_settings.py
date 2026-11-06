"""CLI tests for `know users settings` subcommands. All HTTP mocked."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import scripts.know_cli as cli


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "cli_config.json"
    monkeypatch.setattr(cli, "CONFIG_PATH", cfg_path)
    cli.save_config({
        "admin_url": "http://test-admin:8000",
        "orch_url": "http://test-orch:8002",
        "public_url": "http://test-public:8001",
        "token": "test-token-123",
    })
    return cfg_path


def _run_cli(monkeypatch, argv: list[str]):
    """Run CLI main with args. Returns (exit_code, stdout, stderr)."""
    monkeypatch.setattr(sys, "argv", ["know"] + argv)
    try:
        cli.main()
        return 0, "", ""
    except SystemExit as e:
        return e.code or 0, "", ""


@pytest.fixture
def fake_response():
    """Factory for mock requests.Response."""
    def _make(status_code=200, json_data=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data or {}
        resp.text = json.dumps(json_data or {})
        return resp
    return _make


def test_settings_get_prints_json(isolated_config, monkeypatch, fake_response, capsys):
    expected = {
        "profile": {"username": "u", "active_domains": ["linux"], "active_langs": ["en"]},
        "generation_params": {"articles_per_domain": 10, "questions_types": {"select": 5, "construct": 0}},
        "favorites": ["linux"],
    }
    captured = {}
    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return fake_response(200, expected)
    monkeypatch.setattr(cli.requests, "get", fake_get)

    monkeypatch.setattr(sys, "argv", ["know", "users", "settings", "get"])
    cli.main()

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == expected
    assert "users/me/settings" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer test-token-123"


def test_settings_set_builds_correct_patch(isolated_config, monkeypatch, fake_response, capsys):
    captured = {}
    def fake_put(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return fake_response(200, {"profile": {}, "generation_params": {}, "favorites": []})
    monkeypatch.setattr(cli.requests, "put", fake_put)

    monkeypatch.setattr(sys, "argv", [
        "know", "users", "settings", "set",
        "--articles", "15", "--select", "3", "--construct", "2",
        "--active-domains", "linux,docker",
        "--favorites", "linux",
    ])
    cli.main()

    patch = captured["json"]
    assert patch["active_domains"] == ["linux", "docker"]
    assert patch["favorites"] == ["linux"]
    assert patch["generation_params"]["articles_per_domain"] == 15
    assert patch["generation_params"]["questions_types"] == {"select": 3, "construct": 2}


def test_settings_set_dotted_per_domain(isolated_config, monkeypatch, fake_response):
    captured = {}
    def fake_put(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return fake_response(200, {})
    monkeypatch.setattr(cli.requests, "put", fake_put)

    monkeypatch.setattr(sys, "argv", [
        "know", "users", "settings", "set",
        "--per-domain", "linux.select=10",
        "--per-domain", "linux.articles=20",
        "--per-domain", "docker.construct=2",
    ])
    cli.main()

    per_domain = captured["json"]["generation_params"]["per_domain"]
    assert per_domain["linux"]["questions_types"]["select"] == 10
    assert per_domain["linux"]["articles_per_domain"] == 20
    assert per_domain["docker"]["questions_types"]["construct"] == 2


def test_settings_set_dotted_per_lang(isolated_config, monkeypatch, fake_response):
    captured = {}
    def fake_put(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return fake_response(200, {})
    monkeypatch.setattr(cli.requests, "put", fake_put)

    monkeypatch.setattr(sys, "argv", [
        "know", "users", "settings", "set",
        "--per-lang", "ru.translate=*",
        "--per-lang", "uk.translate=linux,docker",
        "--per-lang", "es.translate=",
    ])
    cli.main()

    per_lang = captured["json"]["generation_params"]["per_lang"]
    assert per_lang["ru"]["translate_domains"] == "*"
    assert per_lang["uk"]["translate_domains"] == ["linux", "docker"]
    assert per_lang["es"]["translate_domains"] == []


def test_settings_set_json_escape(isolated_config, monkeypatch, fake_response):
    captured = {}
    def fake_put(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return fake_response(200, {})
    monkeypatch.setattr(cli.requests, "put", fake_put)

    patch_json = '{"active_domains":["linux"],"favorites":["linux"]}'
    monkeypatch.setattr(sys, "argv", [
        "know", "users", "settings", "set", "--json", patch_json,
    ])
    cli.main()

    assert captured["json"] == {"active_domains": ["linux"], "favorites": ["linux"]}


def test_settings_set_json_and_flags_conflict(isolated_config, monkeypatch, capsys):
    """--json + individual flags → exit 2 with error."""
    monkeypatch.setattr(sys, "argv", [
        "know", "users", "settings", "set", "--json", '{}', "--articles", "10",
    ])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "cannot be combined" in err


def test_settings_set_no_flags_errors(isolated_config, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["know", "users", "settings", "set"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "no settings provided" in capsys.readouterr().err


def test_settings_set_400_shows_detail(isolated_config, monkeypatch, fake_response, capsys):
    def fake_put(url, json=None, headers=None, timeout=None):
        return fake_response(400, {"detail": "articles_per_domain must be integer 1..30"})
    monkeypatch.setattr(cli.requests, "put", fake_put)

    monkeypatch.setattr(sys, "argv", [
        "know", "users", "settings", "set", "--articles", "100",
    ])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "articles_per_domain must be integer 1..30" in capsys.readouterr().err


def test_settings_set_401_shows_auth_error(isolated_config, monkeypatch, fake_response, capsys):
    def fake_put(url, json=None, headers=None, timeout=None):
        return fake_response(401, {})
    monkeypatch.setattr(cli.requests, "put", fake_put)

    monkeypatch.setattr(sys, "argv", [
        "know", "users", "settings", "set", "--articles", "10",
    ])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "auth login" in capsys.readouterr().err


def test_settings_import(tmp_path, isolated_config, monkeypatch, fake_response, capsys):
    patch_file = tmp_path / "patch.json"
    patch_file.write_text(json.dumps({
        "active_domains": ["linux"],
        "generation_params": {"articles_per_domain": 15},
    }), encoding="utf-8")

    captured = {}
    def fake_put(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return fake_response(200, {"profile": {"active_domains": ["linux"]}, "generation_params": {"articles_per_domain": 15}, "favorites": []})
    monkeypatch.setattr(cli.requests, "put", fake_put)

    monkeypatch.setattr(sys, "argv", [
        "know", "users", "settings", "import", str(patch_file),
    ])
    cli.main()

    assert captured["json"]["active_domains"] == ["linux"]
    assert "Imported" in capsys.readouterr().out


def test_settings_export(tmp_path, isolated_config, monkeypatch, fake_response, capsys):
    out_file = tmp_path / "exported.json"
    server_data = {
        "profile": {"username": "u", "active_domains": ["linux", "docker"], "active_langs": ["en"]},
        "generation_params": {"articles_per_domain": 10, "questions_types": {"select": 5, "construct": 0}},
        "favorites": ["linux"],
    }

    def fake_get(url, headers=None, timeout=None):
        return fake_response(200, server_data)
    monkeypatch.setattr(cli.requests, "get", fake_get)

    monkeypatch.setattr(sys, "argv", [
        "know", "users", "settings", "export", str(out_file),
    ])
    cli.main()

    assert out_file.exists()
    exported = json.loads(out_file.read_text())
    assert exported["active_domains"] == ["linux", "docker"]
    assert exported["generation_params"]["articles_per_domain"] == 10
    assert exported["favorites"] == ["linux"]
    assert "username" not in exported
    assert "profile" not in exported


def test_settings_set_pruned_favorites_warning(isolated_config, monkeypatch, fake_response, capsys):
    def fake_put(url, json=None, headers=None, timeout=None):
        return fake_response(200, {
            "profile": {"active_domains": ["linux"]},
            "generation_params": {},
            "favorites": ["linux"],
            "pruned_favorites": ["docker"],
        })
    monkeypatch.setattr(cli.requests, "put", fake_put)

    monkeypatch.setattr(sys, "argv", [
        "know", "users", "settings", "set", "--active-domains", "linux",
    ])
    cli.main()

    err = capsys.readouterr().err
    assert "docker" in err
    assert "auto-removed" in err or "pruned" in err.lower()
