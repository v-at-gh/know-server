"""Unit tests for quizkb.settings."""

import os
from pathlib import Path

import pytest

from quizkb.settings import KnowSettings, get_settings, reset_settings_cache


@pytest.fixture(autouse=True)
def clear_settings_cache_and_env(monkeypatch, tmp_path):
    """Isolate each test from ambient state and other tests."""
    reset_settings_cache()
    monkeypatch.setenv("KNOW_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("KNOW_SESSION_SECRET", raising=False)
    monkeypatch.delenv("DEPLOYMENT_ENV", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_URL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("KNOW_LOGS_ROOT", raising=False)
    yield
    reset_settings_cache()


# def test_dotenv_populates_os_environ(monkeypatch, tmp_path):
#     """If .env has OTEL_..., os.environ must see it after settings import."""
#     env_file = tmp_path / ".env"
#     env_file.write_text(
#         "OTEL_EXPORTER_OTLP_ENDPOINT=http://env-file-collector:4317\n",
#         encoding="utf-8",
#     )
#     monkeypatch.chdir(tmp_path)
#     monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

#     reset_settings_cache()
#     import importlib
#     import quizkb.settings
#     importlib.reload(quizkb.settings)

#     import os
#     assert os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") == "http://env-file-collector:4317"
#     reset_settings_cache()


def test_load_env_file_does_not_override_shell_env(monkeypatch, tmp_path):
    """Shell env must win over .env when override=False (12-factor)."""
    env_file = tmp_path / ".env"
    env_file.write_text("KNOW_TEST_VAR=file-value\n", encoding="utf-8")
    monkeypatch.setenv("KNOW_TEST_VAR", "shell-value")

    from quizkb.settings import _load_env_file
    _load_env_file(env_file)

    assert os.environ["KNOW_TEST_VAR"] == "shell-value"


def test_load_env_file_populates_when_shell_empty(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("KNOW_TEST_VAR=file-value\n", encoding="utf-8")
    monkeypatch.delenv("KNOW_TEST_VAR", raising=False)

    from quizkb.settings import _load_env_file
    _load_env_file(env_file)

    assert os.environ["KNOW_TEST_VAR"] == "file-value"


def test_dotenv_loaded_at_module_import():
    """Fresh subprocess, .env at REPO_ROOT, clean env — verify os.environ
    populated by module import alone. Catches: module-level call removed."""
    import subprocess, sys
    from quizkb.settings import REPO_ROOT

    env_file = REPO_ROOT / ".env"
    backup = env_file.read_text(encoding="utf-8") if env_file.exists() else None

    env_file.write_text(
        "KNOW_TEST_IMPORT_VAR=loaded-from-dotenv\n",
        encoding="utf-8",
    )
    # Ensure parent process env doesn't leak the var into subprocess
    env = {k: v for k, v in os.environ.items() if k != "KNOW_TEST_IMPORT_VAR"}

    try:
        result = subprocess.run(
            [sys.executable, "-c",
             "import os, quizkb.settings; "
             "print(os.environ.get('KNOW_TEST_IMPORT_VAR', 'MISSING'))"],
            capture_output=True, text=True, cwd=str(REPO_ROOT), env=env,
        )
        assert "loaded-from-dotenv" in result.stdout, (
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    finally:
        if backup is not None:
            env_file.write_text(backup, encoding="utf-8")
        else:
            env_file.unlink(missing_ok=True)


def test_otel_endpoint_from_repo_env_file(tmp_path):
    """End-to-end: .env at REPO_ROOT populates OTEL_EXPORTER_OTLP_ENDPOINT
    by the time settings module finishes importing."""
    import subprocess, sys
    from quizkb.settings import REPO_ROOT

    real_env = REPO_ROOT / ".env"
    if real_env.exists():
        pytest.skip("Repo .env exists; skipping to avoid clobbering")

    real_env.write_text(
        "OTEL_EXPORTER_OTLP_ENDPOINT=http://env-file-collector:4317\n",
        encoding="utf-8",
    )
    try:
        # Subprocess with clean env (no shell OTEL var), fresh import
        env = {k: v for k, v in os.environ.items() if k != "OTEL_EXPORTER_OTLP_ENDPOINT"}
        result = subprocess.run(
            [sys.executable, "-c",
             "import os, quizkb.settings; "
             "print(os.environ.get('OTEL_EXPORTER_OTLP_ENDPOINT', 'MISSING'))"],
            capture_output=True, text=True, cwd=str(REPO_ROOT), env=env,
        )
        assert "http://env-file-collector:4317" in result.stdout, (
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    finally:
        real_env.unlink(missing_ok=True)


def test_defaults_dev():
    s = KnowSettings()
    assert s.deployment_env == "dev"
    assert s.effective_env == "dev"
    assert s.orchestrator_url == "http://127.0.0.1:8002"
    assert s.ollama_host == "http://127.0.0.1:11434"
    assert s.session_secret is not None  # auto-resolved
    assert s.session_secret.get_secret_value() != ""


def test_env_override_orchestrator_url(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orch.prod:9000")
    s = KnowSettings()
    assert s.orchestrator_url == "http://orch.prod:9000"


def test_invalid_url_raises(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_URL", "not-a-url")
    with pytest.raises(ValueError, match="must start with http"):
        KnowSettings()


def test_trailing_slash_stripped(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orch.prod:9000/")
    s = KnowSettings()
    assert s.orchestrator_url == "http://orch.prod:9000"


def test_case_insensitive_env(monkeypatch):
    monkeypatch.setenv("orchestrator_url", "http://custom:9999")
    s = KnowSettings()
    assert s.orchestrator_url == "http://custom:9999"


def test_knw_logs_root_backward_compat(monkeypatch, tmp_path):
    """KNOW_LOGS_ROOT must override logs_base."""
    monkeypatch.setenv("KNOW_LOGS_ROOT", str(tmp_path / "custom-logs"))
    s = KnowSettings()
    assert s.logs_base == tmp_path / "custom-logs"
    assert s.logs_root == tmp_path / "custom-logs" / "runs"


def test_session_secret_env_override(monkeypatch):
    monkeypatch.setenv("KNOW_SESSION_SECRET", "explicit-secret-xyz")
    s = KnowSettings()
    assert s.session_secret.get_secret_value() == "explicit-secret-xyz"


def test_session_secret_auto_generated_persists(monkeypatch, tmp_path):
    """First Settings() generates and writes file; second reads it."""
    state = tmp_path / "state"
    monkeypatch.setenv("KNOW_STATE_DIR", str(state))
    s1 = KnowSettings()
    secret1 = s1.session_secret.get_secret_value()
    secret_file = state / ".session_secret"
    assert secret_file.exists()
    assert secret_file.read_text().strip() == secret1

    reset_settings_cache()
    s2 = KnowSettings()
    secret2 = s2.session_secret.get_secret_value()
    assert secret2 == secret1  # persisted, not regenerated


def test_prod_without_secret_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("DEPLOYMENT_ENV", "prod")
    monkeypatch.setenv("KNOW_STATE_DIR", str(tmp_path / "state"))
    with pytest.raises(ValueError, match="KNOW_SESSION_SECRET must be set"):
        KnowSettings()


def test_prod_with_secret_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("DEPLOYMENT_ENV", "prod")
    monkeypatch.setenv("KNOW_SESSION_SECRET", "prod-secret-abc")
    monkeypatch.setenv("KNOW_STATE_DIR", str(tmp_path / "state"))
    s = KnowSettings()
    assert s.is_prod is True
    assert s.session_secret.get_secret_value() == "prod-secret-abc"


def test_prod_ignores_file_secret(monkeypatch, tmp_path):
    """In prod, if env unset but state/.session_secret exists, still raise."""
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / ".session_secret").write_text("file-based-secret", encoding="utf-8")
    monkeypatch.setenv("DEPLOYMENT_ENV", "prod")
    monkeypatch.setenv("KNOW_STATE_DIR", str(state))
    with pytest.raises(ValueError, match="KNOW_SESSION_SECRET must be set"):
        KnowSettings()


def test_unimplemented_env_falls_back_to_dev(monkeypatch, tmp_path):
    monkeypatch.setenv("DEPLOYMENT_ENV", "docker")
    monkeypatch.setenv("KNOW_STATE_DIR", str(tmp_path / "state"))
    s = KnowSettings()
    assert s.deployment_env == "docker"
    assert s.effective_env == "dev"
    assert s.is_prod is False
    assert s.session_secret is not None


def test_secret_str_redacted_in_repr(monkeypatch):
    monkeypatch.setenv("KNOW_SESSION_SECRET", "very-private-xyz")
    s = KnowSettings()
    r = repr(s)
    assert "very-private-xyz" not in r
    assert "**********" in r or "SecretStr" in r


def test_cache_returns_same_instance():
    reset_settings_cache()
    a = get_settings()
    b = get_settings()
    assert a is b


def test_extra_env_ignored(monkeypatch, tmp_path):
    """Real shell env vars (HOME, PATH, etc.) must not cause failure."""
    monkeypatch.setenv("SOME_RANDOM_ENV_VAR", "should-be-ignored")
    monkeypatch.setenv("KNOW_STATE_DIR", str(tmp_path / "state"))
    s = KnowSettings()  # should not raise
    assert s.deployment_env == "dev"
