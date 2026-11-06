"""CLI tests for know packs * subcommands. HTTP mocked."""

import json
import sys
from unittest.mock import MagicMock
import pytest


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    import scripts.know_cli as cli
    cfg_path = tmp_path / "cli_config.json"
    monkeypatch.setattr(cli, "CONFIG_PATH", cfg_path)
    cli.save_config({
        "admin_url": "http://test-admin:8000",
        "orch_url": "http://test-orch:8002",
        "public_url": "http://test-public:8001",
        "token": "test-token-123",
    })
    return cfg_path


@pytest.fixture
def fake_response():
    def _make(status_code=200, json_data=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data or {}
        resp.text = json.dumps(json_data or {})
        resp.headers = {"content-type": "application/json"}
        return resp
    return _make


def _run_cli(monkeypatch, argv):
    import scripts.know_cli as cli
    monkeypatch.setattr(sys, "argv", ["know"] + argv)
    try:
        cli.main()
        return 0
    except SystemExit as e:
        return e.code or 0


def test_packs_list_prints_table(isolated_config, monkeypatch, fake_response, capsys):
    payload = {
        "total": 1,
        "schema_version": 1,
        "packs": [{
            "definition": {"id": "p1", "kind": "campaign",
                           "domains": ["a"], "languages": ["en"], "campaigns": []},
            "built": None,
            "built_is_stale": False,
        }],
    }
    import scripts.know_cli as cli
    captured = {}
    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        return fake_response(200, payload)
    monkeypatch.setattr(cli.requests, "get", fake_get)

    _run_cli(monkeypatch, ["packs", "list"])
    out = capsys.readouterr().out
    assert "p1" in out
    assert "(not built)" in out
    assert "/api/v1/packs" in captured["url"]


def test_packs_create_with_flags(isolated_config, monkeypatch, fake_response):
    captured = {}
    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return fake_response(201, {"status": "created"})
    import scripts.know_cli as cli
    monkeypatch.setattr(cli.requests, "post", fake_post)

    _run_cli(monkeypatch, ["packs", "create", "--id", "new1",
                           "--kind", "campaign",
                           "--domains", "linux,docker",
                           "--languages", "en,ru"])
    assert captured["json"]["id"] == "new1"
    assert captured["json"]["domains"] == ["linux", "docker"]
    assert captured["json"]["languages"] == ["en", "ru"]
    assert captured["json"]["campaigns"] == []  # default empty, no drift


def test_packs_edit_from_file(isolated_config, monkeypatch, fake_response, tmp_path, capsys):
    patch_file = tmp_path / "patch.json"
    patch_file.write_text(json.dumps({"domains": ["python"]}), encoding="utf-8")
    captured = {}
    def fake_patch(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return fake_response(200, {"status": "updated"})
    import scripts.know_cli as cli
    monkeypatch.setattr(cli.requests, "patch", fake_patch)

    _run_cli(monkeypatch, ["packs", "edit", "target", "--from-file", str(patch_file)])
    assert captured["json"] == {"domains": ["python"]}


def test_packs_delete_requires_confirmation(isolated_config, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    import scripts.know_cli as cli
    rc = _run_cli(monkeypatch, ["packs", "delete", "abc"])
    assert rc == 1
    out = capsys.readouterr()
    assert "Aborted" in out.out


def test_packs_delete_yes_removes(isolated_config, monkeypatch, fake_response):
    captured = {}
    def fake_delete(url, headers=None, timeout=None):
        captured["url"] = url
        return fake_response(200, {"definition_removed": True, "artifact_removed": True})
    import scripts.know_cli as cli
    monkeypatch.setattr(cli.requests, "delete", fake_delete)

    _run_cli(monkeypatch, ["packs", "delete", "xyz", "--yes"])
    assert "delete_artifact=true" in captured["url"]


def test_packs_build_local_script(isolated_config, monkeypatch, tmp_path):
    calls = []
    class FakeProc:
        def __init__(self, returncode=0):
            self.returncode = returncode
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc()
    import scripts.know_cli as cli
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    rc = _run_cli(monkeypatch, ["packs", "build", "mypack"])
    assert rc == 0
    assert any("make_packs.py" in str(c) for c in calls)
    assert any("--pack-id" in c and "mypack" in c for c in calls if isinstance(c, list))
