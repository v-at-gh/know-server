"""Tests for CLI pack selection argument parsing."""

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


def test_parse_selection_single_domain():
    from scripts.know_cli import _parse_selection_args
    res = _parse_selection_args(["alpha=art-1,art-2:3"])
    assert res == {
        "alpha": {"articles": ["art-1", "art-2"], "questions_per_article": 3}
    }


def test_parse_selection_default_qpa():
    from scripts.know_cli import _parse_selection_args
    res = _parse_selection_args(["alpha=art-1"], default_qpa=5)
    assert res == {
        "alpha": {"articles": ["art-1"], "questions_per_article": 5}
    }


def test_parse_selection_multiple_domains():
    from scripts.know_cli import _parse_selection_args
    res = _parse_selection_args(["alpha=art-1:3", "beta=art-2:10"])
    assert res["alpha"]["questions_per_article"] == 3
    assert res["beta"]["articles"] == ["art-2"]
    assert res["beta"]["questions_per_article"] == 10


def test_parse_selection_invalid_no_equals():
    from scripts.know_cli import _parse_selection_args
    with pytest.raises(ValueError, match="invalid selection format"):
        _parse_selection_args(["alpha"])


def test_parse_selection_invalid_qpa_zero():
    from scripts.know_cli import _parse_selection_args
    with pytest.raises(ValueError, match="must be >= 1"):
        _parse_selection_args(["alpha=art-1:0"])


def test_parse_selection_invalid_empty_articles():
    from scripts.know_cli import _parse_selection_args
    with pytest.raises(ValueError, match="at least one article slug required"):
        _parse_selection_args(["alpha=:5"])


def test_cli_create_with_selection(isolated_config, monkeypatch, fake_response):
    captured = {}
    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return fake_response(201, {"status": "created", "id": "p"})
    import scripts.know_cli as cli
    monkeypatch.setattr(cli.requests, "post", fake_post)

    rc = _run_cli(monkeypatch, ["packs", "create", "--id", "sel1", "--domains", "alpha", "--selection", "alpha=art-1:3"])
    assert rc == 0
    assert captured["json"]["selection"] == {
        "alpha": {"articles": ["art-1"], "questions_per_article": 3}
    }


def test_cli_edit_with_selection(isolated_config, monkeypatch, fake_response):
    captured = {}
    def fake_patch(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return fake_response(200, {"status": "updated", "pack": {}})
    import scripts.know_cli as cli
    monkeypatch.setattr(cli.requests, "patch", fake_patch)

    rc = _run_cli(monkeypatch, ["packs", "edit", "sel1", "--selection", "alpha=art-2:4"])
    assert rc == 0
    assert captured["json"]["selection"] == {
        "alpha": {"articles": ["art-2"], "questions_per_article": 4}
    }


def test_cli_edit_no_selection(isolated_config, monkeypatch, fake_response):
    captured = {}
    def fake_patch(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return fake_response(200, {"status": "updated", "pack": {}})
    import scripts.know_cli as cli
    monkeypatch.setattr(cli.requests, "patch", fake_patch)

    rc = _run_cli(monkeypatch, ["packs", "edit", "sel1", "--no-selection"])
    assert rc == 0
    assert captured["json"]["selection"] == {}


def test_cli_from_file_exclusive_with_selection_create(isolated_config, monkeypatch, tmp_path):
    f = tmp_path / "pack.json"
    f.write_text('{"id": "x"}')
    called = 0
    def fake_post(*args, **kwargs):
        nonlocal called
        called += 1
        return MagicMock(status_code=201)
    import scripts.know_cli as cli
    monkeypatch.setattr(cli.requests, "post", fake_post)

    rc = _run_cli(monkeypatch, ["packs", "create", "--id", "x", "--from-file", str(f), "--selection", "alpha=art-1"])
    assert rc == 2
    assert called == 0


def test_cli_from_file_exclusive_with_selection_edit(isolated_config, monkeypatch, tmp_path):
    f = tmp_path / "patch.json"
    f.write_text('{"kind": "custom"}')
    called = 0
    def fake_patch(*args, **kwargs):
        nonlocal called
        called += 1
        return MagicMock(status_code=200)
    import scripts.know_cli as cli
    monkeypatch.setattr(cli.requests, "patch", fake_patch)

    rc = _run_cli(monkeypatch, ["packs", "edit", "x", "--from-file", str(f), "--selection", "alpha=art-1"])
    assert rc == 2
    assert called == 0
