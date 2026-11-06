"""Scaffold generation lifecycle tests."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from quizkb.settings import KnowSettings, reset_settings_cache, REPO_ROOT


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Allow scaffold to trigger by removing env override vars, but keep
    KNOW_STATE_DIR for test isolation -- note: KNOW_STATE_DIR itself IS in
    _ENV_OVERRIDE_VARS, so scaffold skips. Tests below that need scaffold
    to trigger must monkeypatch _env_override_present too."""
    reset_settings_cache()
    monkeypatch.setenv("KNOW_STATE_DIR", str(tmp_path / "state"))
    for k in ("ORCHESTRATOR_URL", "OLLAMA_HOST", "ADMIN_URL", "PUBLIC_URL",
              "KNOW_OUTPUT_ROOT", "KNOW_LOGS_ROOT", "KNOW_SESSION_SECRET",
              "DEPLOYMENT_ENV", "KNOW_CONFIG_VERSION"):
        monkeypatch.delenv(k, raising=False)
    yield
    reset_settings_cache()


def test_scaffold_skipped_when_env_override(monkeypatch, tmp_path):
    """R4: env override present = no scaffold."""
    monkeypatch.setattr(
        "quizkb.settings._yaml_config_path",
        lambda: tmp_path / "state" / "know.config.yaml"
    )
    KnowSettings()  # KNOW_STATE_DIR is set -> skip
    assert not (tmp_path / "state" / "know.config.yaml").exists()


def test_scaffold_generates_when_no_env(monkeypatch, tmp_path):
    """R4: no env override + file absent = scaffold generated."""
    state_dir = tmp_path / "state"
    monkeypatch.setattr(
        "quizkb.settings._yaml_config_path",
        lambda: state_dir / "know.config.yaml"
    )
    monkeypatch.setattr("quizkb.settings._env_override_present", lambda: False)
    monkeypatch.setenv("KNOW_STATE_DIR", str(state_dir))
    KnowSettings()
    assert (state_dir / "know.config.yaml").exists()
    content = (state_dir / "know.config.yaml").read_text()
    assert "know_config_version: 1" in content


def test_scaffold_skipped_for_docker(monkeypatch, tmp_path):
    """R7: docker/k8s env skips scaffold."""
    state_dir = tmp_path / "state"
    monkeypatch.setattr(
        "quizkb.settings._yaml_config_path",
        lambda: state_dir / "know.config.yaml"
    )
    monkeypatch.setattr("quizkb.settings._env_override_present", lambda: False)
    monkeypatch.setenv("DEPLOYMENT_ENV", "docker")
    monkeypatch.setenv("KNOW_STATE_DIR", str(state_dir))
    KnowSettings()
    assert not (state_dir / "know.config.yaml").exists()


def test_scaffold_idempotent(monkeypatch, tmp_path):
    """Existing file not overwritten."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    existing = state_dir / "know.config.yaml"
    existing.write_text("know_config_version: 1\ndeployment_env: dev\n")
    original_content = existing.read_text()

    monkeypatch.setattr("quizkb.settings._yaml_config_path", lambda: existing)
    monkeypatch.setattr("quizkb.settings._env_override_present", lambda: False)
    monkeypatch.setenv("KNOW_STATE_DIR", str(state_dir))
    KnowSettings()
    assert existing.read_text() == original_content


def test_scaffold_copies_example_file(monkeypatch, tmp_path):
    """Scaffold content = example file content."""
    state_dir = tmp_path / "state"
    example_file = tmp_path / "example.yaml"
    example_file.write_text("# Custom example\nknow_config_version: 1\n")

    monkeypatch.setattr(
        "quizkb.settings._yaml_config_path",
        lambda: state_dir / "know.config.yaml"
    )
    monkeypatch.setattr("quizkb.settings._yaml_example_path", lambda: example_file)
    monkeypatch.setattr("quizkb.settings._env_override_present", lambda: False)
    monkeypatch.setenv("KNOW_STATE_DIR", str(state_dir))
    KnowSettings()
    assert (state_dir / "know.config.yaml").read_text() == "# Custom example\nknow_config_version: 1\n"


def test_scaffold_generated_in_fresh_subprocess(tmp_path):
    """Fresh process, no REPO_ROOT/state/know.config.yaml, no env overrides,
    scaffold should be created by get_settings()."""
    real_yaml = REPO_ROOT / "state" / "know.config.yaml"
    env_file = REPO_ROOT / ".env"
    yaml_backup = real_yaml.read_text() if real_yaml.exists() else None
    env_backup = env_file.read_text() if env_file.exists() else None

    if real_yaml.exists():
        real_yaml.unlink()
    if env_file.exists():
        env_file.unlink()

    clean = {k: v for k, v in os.environ.items() if k not in (
        "ORCHESTRATOR_URL", "OLLAMA_HOST", "ADMIN_URL", "PUBLIC_URL",
        "KNOW_STATE_DIR", "KNOW_OUTPUT_ROOT", "KNOW_LOGS_ROOT",
        "KNOW_SESSION_SECRET", "DEPLOYMENT_ENV", "KNOW_CONFIG_VERSION",
    )}
    try:
        subprocess.run(
            [sys.executable, "-c", "from quizkb.settings import get_settings; get_settings()"],
            cwd=str(REPO_ROOT), env=clean, check=True,
        )
        assert real_yaml.exists(), "scaffold not created in subprocess"
        assert "know_config_version" in real_yaml.read_text()
    finally:
        if real_yaml.exists():
            real_yaml.unlink()
        if yaml_backup is not None:
            real_yaml.write_text(yaml_backup)
        if env_backup is not None:
            env_file.write_text(env_backup)
