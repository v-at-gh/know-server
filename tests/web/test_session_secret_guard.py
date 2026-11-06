"""Verify web.app startup validation refuses to start in prod without
KNOW_SESSION_SECRET, and starts cleanly in dev without it.

Uses subprocess isolation rather than `importlib.reload(web.app)`. Reload
creates a fresh `web.app.app` FastAPI instance and new module-level names
(`app`, `get_current_user`, `_settings`, `ORCHESTRATOR_URL`), orphaning
any `from web.app import app` bindings held by already-collected test
modules. This caused a cascade of 303-redirect failures across unrelated
tests when a polluter test executed early in the suite.

Subprocess is slower (~2-3s per run) but guarantees process-level
isolation. Any test that needs to verify module-import-time side effects
of `web.app` should use this pattern rather than reload.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from quizkb.settings import REPO_ROOT


def _run_import_web_app(*, extra_env: dict[str, str], clean_env: bool = True) -> subprocess.CompletedProcess:
    """Run `python -c 'import web.app'` in a subprocess with isolated env.

    Cleans KNOW_* / OTEL_* / DEPLOYMENT_ENV from parent env, then applies
    `extra_env`. Ensures the subprocess sees only the configuration we
    explicitly set.
    """
    base_env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("KNOW_")
        and not k.startswith("OTEL_")
        and k not in ("DEPLOYMENT_ENV", "SESSION_SECRET", "ORCHESTRATOR_URL",
                      "OLLAMA_HOST", "ADMIN_URL", "PUBLIC_URL")
    } if clean_env else dict(os.environ)
    base_env.update(extra_env)

    return subprocess.run(
        [sys.executable, "-c", "import web.app"],
        cwd=str(REPO_ROOT),
        env=base_env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_prod_startup_without_secret_raises(tmp_path):
    """DEPLOYMENT_ENV=prod + no KNOW_SESSION_SECRET -> import must fail
    with actionable ValueError message on stderr."""
    result = _run_import_web_app(
        extra_env={
            "DEPLOYMENT_ENV": "prod",
            "KNOW_STATE_DIR": str(tmp_path / "state"),
        },
    )
    assert result.returncode != 0, (
        f"expected non-zero exit in prod without secret; "
        f"got rc=0, stdout={result.stdout!r}, stderr={result.stderr!r}"
    )
    assert "KNOW_SESSION_SECRET must be set" in result.stderr, (
        f"expected actionable error in stderr; got stderr={result.stderr!r}"
    )


def test_dev_startup_without_secret_ok(tmp_path):
    """DEPLOYMENT_ENV=dev + no KNOW_SESSION_SECRET -> import must succeed
    and generate state/.session_secret in the isolated state dir."""
    state_dir = tmp_path / "state"
    result = _run_import_web_app(
        extra_env={
            "DEPLOYMENT_ENV": "dev",
            "KNOW_STATE_DIR": str(state_dir),
        },
    )
    assert result.returncode == 0, (
        f"expected rc=0 in dev without secret; got rc={result.returncode}, "
        f"stderr={result.stderr!r}"
    )
    assert (state_dir / ".session_secret").exists(), (
        "dev startup should auto-generate session secret in isolated state dir"
    )


def test_prod_with_secret_starts_ok(tmp_path):
    """DEPLOYMENT_ENV=prod + KNOW_SESSION_SECRET provided -> import succeeds.
    Regression guard: prod-with-secret path was previously only tested
    indirectly via in-process reload."""
    result = _run_import_web_app(
        extra_env={
            "DEPLOYMENT_ENV": "prod",
            "KNOW_SESSION_SECRET": "test-prod-secret-xyz",
            "KNOW_STATE_DIR": str(tmp_path / "state"),
        },
    )
    assert result.returncode == 0, (
        f"expected rc=0 in prod with secret; got rc={result.returncode}, "
        f"stderr={result.stderr!r}"
    )
