"""Unit + subprocess tests for YAML config source."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from quizkb.settings import (
    KnowSettings, reset_settings_cache, REPO_ROOT,
)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """Standard isolation: fresh cache, custom state dir, no ambient env."""
    reset_settings_cache()
    monkeypatch.setenv("KNOW_STATE_DIR", str(tmp_path / "state"))
    for k in ("ORCHESTRATOR_URL", "OLLAMA_HOST", "ADMIN_URL",
              "PUBLIC_URL", "KNOW_OUTPUT_ROOT", "KNOW_LOGS_ROOT",
              "KNOW_SESSION_SECRET", "DEPLOYMENT_ENV", "KNOW_CONFIG_VERSION"):
        monkeypatch.delenv(k, raising=False)
    yield
    reset_settings_cache()


def test_yaml_source_loads_value(monkeypatch, tmp_path):
    """YAML value picked up when env not set."""
    yaml_file = tmp_path / "know.config.yaml"
    yaml_file.write_text("orchestrator_url: http://yaml-orch:9999\n")
    monkeypatch.setattr(
        "quizkb.settings._yaml_config_path", lambda: yaml_file
    )
    reset_settings_cache()
    s = KnowSettings()
    assert s.orchestrator_url == "http://yaml-orch:9999"


def test_yaml_loses_to_env(monkeypatch, tmp_path):
    """Env var wins over YAML per R2 precedence."""
    yaml_file = tmp_path / "know.config.yaml"
    yaml_file.write_text("orchestrator_url: http://yaml-orch:9999\n")
    monkeypatch.setattr("quizkb.settings._yaml_config_path", lambda: yaml_file)
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://env-orch:8888")
    reset_settings_cache()
    s = KnowSettings()
    assert s.orchestrator_url == "http://env-orch:8888"


def test_yaml_unknown_key_warns_with_suggestion(monkeypatch, tmp_path, caplog):
    """R5: unknown key warns with close-match suggestion, does not fail."""
    yaml_file = tmp_path / "know.config.yaml"
    yaml_file.write_text(
        "orchestrator_url: http://valid:9000\n"
        "orchestrator_ulr: http://typo:9999\n"
    )
    monkeypatch.setattr("quizkb.settings._yaml_config_path", lambda: yaml_file)
    reset_settings_cache()
    with caplog.at_level("WARNING"):
        s = KnowSettings()
    assert s.orchestrator_url == "http://valid:9000"
    warning_records = [r for r in caplog.records if "config_unknown_key" in r.message]
    assert len(warning_records) == 1
    rec = warning_records[0]
    assert getattr(rec, "key", None) == "orchestrator_ulr"
    assert getattr(rec, "suggestion", None) == "orchestrator_url"
    assert "orchestrator_ulr" in rec.message
    assert "orchestrator_url" in rec.message


def test_yaml_known_future_key_not_warned(monkeypatch, tmp_path, caplog):
    """Fields declared in KnowSettings (like know_config_version) don't
    trigger unknown-key warning even if value is unexpected."""
    yaml_file = tmp_path / "know.config.yaml"
    yaml_file.write_text("know_config_version: 2\n")
    monkeypatch.setattr("quizkb.settings._yaml_config_path", lambda: yaml_file)
    reset_settings_cache()
    with caplog.at_level("WARNING"):
        s = KnowSettings()
    assert s.know_config_version == 2
    unknown_records = [r for r in caplog.records if "config_unknown_key" in r.message]
    assert len(unknown_records) == 0
    version_records = [r for r in caplog.records if "config_version_newer_than_supported" in r.message]
    assert len(version_records) == 1


def test_malformed_yaml_raises_actionable_error(monkeypatch, tmp_path):
    """Q5: malformed YAML fails loud with file path."""
    yaml_file = tmp_path / "know.config.yaml"
    yaml_file.write_text("foo: [unclosed\n")
    monkeypatch.setattr("quizkb.settings._yaml_config_path", lambda: yaml_file)
    reset_settings_cache()
    with pytest.raises(ValueError, match=r"Invalid YAML in .*know\.config\.yaml"):
        KnowSettings()


def test_relative_path_resolved_against_repo_root(monkeypatch, tmp_path):
    """Relative paths normalize to REPO_ROOT, not cwd."""
    yaml_file = tmp_path / "know.config.yaml"
    yaml_file.write_text("output_root: custom-output\n")
    monkeypatch.setattr("quizkb.settings._yaml_config_path", lambda: yaml_file)
    monkeypatch.delenv("KNOW_OUTPUT_ROOT", raising=False)
    reset_settings_cache()
    s = KnowSettings()
    assert s.output_root == REPO_ROOT / "custom-output"


def test_tilde_path_expanded(monkeypatch, tmp_path):
    """~ in YAML path expands to home."""
    yaml_file = tmp_path / "know.config.yaml"
    yaml_file.write_text("output_root: ~/know-output\n")
    monkeypatch.setattr("quizkb.settings._yaml_config_path", lambda: yaml_file)
    monkeypatch.delenv("KNOW_OUTPUT_ROOT", raising=False)
    reset_settings_cache()
    s = KnowSettings()
    assert s.output_root == Path.home() / "know-output"


def test_yaml_session_secret_warns(monkeypatch, tmp_path, caplog):
    """R6: session_secret in YAML logs warning in dev."""
    yaml_file = tmp_path / "know.config.yaml"
    yaml_file.write_text("session_secret: yaml-provided-secret\n")
    monkeypatch.setattr("quizkb.settings._yaml_config_path", lambda: yaml_file)
    monkeypatch.setenv("KNOW_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("KNOW_SESSION_SECRET", raising=False)
    reset_settings_cache()
    with caplog.at_level("WARNING"):
        s = KnowSettings()
    assert s.session_secret.get_secret_value() == "yaml-provided-secret"
    warning_records = [r for r in caplog.records if "session_secret_from_yaml" in r.message]
    assert len(warning_records) == 1
    assert "KNOW_SESSION_SECRET" in warning_records[0].message


def test_prod_yaml_secret_still_raises(monkeypatch, tmp_path):
    """R6: in prod, YAML secret does NOT satisfy requirement."""
    yaml_file = tmp_path / "know.config.yaml"
    yaml_file.write_text("session_secret: yaml-provided-secret\n")
    monkeypatch.setattr("quizkb.settings._yaml_config_path", lambda: yaml_file)
    monkeypatch.setenv("DEPLOYMENT_ENV", "prod")
    monkeypatch.setenv("KNOW_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("KNOW_SESSION_SECRET", raising=False)
    reset_settings_cache()
    with pytest.raises(ValueError, match="KNOW_SESSION_SECRET must be set"):
        KnowSettings()


def test_subprocess_yaml_test_isolated_from_ambient_dotenv():
    """Meta-test: document invariant that ambient .env is isolated in subprocess tests."""
    pytest.skip("Documented invariant; verified by test_yaml_loaded_in_fresh_subprocess in isolation")


def test_yaml_loaded_in_fresh_subprocess(tmp_path):
    """End-to-end: fresh process, YAML at default path, verify it's used.
    Isolates .env file so ambient config doesn't leak into subprocess."""
    yaml_file = REPO_ROOT / "state" / "know.config.yaml"
    env_file = REPO_ROOT / ".env"
    yaml_backup = yaml_file.read_text() if yaml_file.exists() else None
    env_backup = env_file.read_text() if env_file.exists() else None

    yaml_file.parent.mkdir(parents=True, exist_ok=True)
    yaml_file.write_text("orchestrator_url: http://subprocess-test:9999\n")
    if env_file.exists():
        env_file.unlink()

    env = {k: v for k, v in os.environ.items()
           if k not in ("ORCHESTRATOR_URL", "KNOW_STATE_DIR")}
    try:
        result = subprocess.run(
            [sys.executable, "-c",
             "from quizkb.settings import get_settings; "
             "print(get_settings().orchestrator_url)"],
            capture_output=True, text=True, cwd=str(REPO_ROOT), env=env,
        )
        assert "http://subprocess-test:9999" in result.stdout, (
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    finally:
        if yaml_backup is not None:
            yaml_file.write_text(yaml_backup)
        else:
            yaml_file.unlink(missing_ok=True)
        if env_backup is not None:
            env_file.write_text(env_backup)
