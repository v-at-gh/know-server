"""Runtime settings loaded from environment (and .env and YAML) with validation.

Consumers import get_settings(). lru_cache ensures single instance per
process. Env vars are read at first call; changing env at runtime does
not update existing instance (startup-time configuration by design).

Session secret resolution (in priority order):
  1. KNOW_SESSION_SECRET env var -- always wins; required for prod
  2. state/.session_secret file   -- dev/staging persistence
  3. Auto-generated on first run  -- written to state/.session_secret
  4. In prod, if env unset        -- raise, refuse to start

Deployment env values: dev, prod (fully implemented).
  staging, docker, k8s -- accepted but fall back to dev behavior in S1
  with a startup warning. See _effective_env().

Backward-compat:
  All existing env var names preserved (KNOW_LOGS_ROOT, OLLAMA_HOST,
  OTEL_EXPORTER_OTLP_ENDPOINT, DEPLOYMENT_ENV, KNOW_AUTO_REINDEX,
  KNOW_SESSION_SECRET).
"""

from __future__ import annotations

import difflib
import logging
import os
import secrets
import yaml
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    YamlConfigSettingsSource,
    PydanticBaseSettingsSource,
)
from dotenv import load_dotenv

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent


def _load_env_file(path: Path) -> None:
    """Load .env into os.environ. Existing shell env wins (override=False).
    No-op if file missing. Explicit path -- load_dotenv() without args would
    walk from this module's file location, not cwd, silently finding nothing
    when the file lives elsewhere."""
    if path.exists():
        load_dotenv(path, override=False)


_load_env_file(REPO_ROOT / ".env")

_DeploymentEnv = Literal["dev", "prod", "staging", "docker", "k8s"]
_IMPLEMENTED_ENVS = frozenset({"dev", "prod"})


def _yaml_config_path() -> Path:
    return REPO_ROOT / "state" / "know.config.yaml"


def _yaml_example_path() -> Path:
    return REPO_ROOT / "state" / "know.config.example.yaml"


def _validate_yaml_syntax(path: Path) -> None:
    if not path.exists():
        return
    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(
            f"Invalid YAML in {path}: {e}. "
            f"Fix the syntax or delete the file to use defaults."
        ) from e


def _yaml_has_session_secret() -> bool:
    """True if the active YAML config file has a top-level session_secret
    key. Used to emit a security warning when secret is sourced from YAML."""
    path = _yaml_config_path()
    if not path.exists():
        return False
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
        return isinstance(content, dict) and "session_secret" in content
    except yaml.YAMLError:
        return False


class _WarningYamlSource(YamlConfigSettingsSource):
    """YamlConfigSettingsSource that warns on unknown keys with typo
    suggestions. Does not fail — R5 requires resilience."""

    def __call__(self) -> dict:
        data = super().__call__()
        if not data:
            return data
        known = set()
        for field_name, field_info in KnowSettings.model_fields.items():
            known.add(field_name)
            known.add(field_name.lower())
            alias = field_info.validation_alias
            if alias:
                if isinstance(alias, str):
                    known.add(alias)
                    known.add(alias.lower())
                elif hasattr(alias, "choices"):
                    for choice in alias.choices:
                        if isinstance(choice, str):
                            known.add(choice)
                            known.add(choice.lower())

        field_names = list(KnowSettings.model_fields.keys())
        for key in data.keys():
            if key in known or key.lower() in known:
                continue
            matches = difflib.get_close_matches(key, field_names, n=1, cutoff=0.6)
            suggestion = matches[0] if matches else None
            log.warning(
                f"config_unknown_key: '{key}' in {_yaml_config_path()}"
                + (f" (did you mean '{suggestion}'?)" if suggestion else " (no close match)"),
                extra={
                    "key": key,
                    "suggestion": suggestion,
                    "file": str(_yaml_config_path()),
                },
            )
        return data


class KnowSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Schema version ---
    know_config_version: int = Field(
        default=1,
        validation_alias=AliasChoices("KNOW_CONFIG_VERSION", "know_config_version"),
    )

    # --- Deployment ---
    deployment_env: _DeploymentEnv = "dev"

    # --- Service URLs ---
    ollama_host: str = "http://127.0.0.1:11434"
    admin_url: str = "http://127.0.0.1:8000"
    public_url: str = "http://127.0.0.1:8001"
    orchestrator_url: str = "http://127.0.0.1:8002"

    # --- Session ---
    session_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("KNOW_SESSION_SECRET", "SESSION_SECRET", "session_secret"),
    )

    # --- Paths ---
    state_dir: Path = Field(
        default_factory=lambda: REPO_ROOT / "state",
        validation_alias=AliasChoices("KNOW_STATE_DIR", "STATE_DIR", "state_dir"),
    )
    output_root: Path = Field(
        default_factory=lambda: REPO_ROOT / "output",
        validation_alias=AliasChoices("KNOW_OUTPUT_ROOT", "OUTPUT_ROOT", "output_root"),
    )
    logs_base: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("KNOW_LOGS_ROOT", "LOGS_BASE", "logs_base"),
    )

    # ---------- Sources Customisation ----------

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_path = _yaml_config_path()
        _validate_yaml_syntax(yaml_path)
        return (
            init_settings,
            env_settings,
            _WarningYamlSource(settings_cls, yaml_file=yaml_path),
            dotenv_settings,
            file_secret_settings,
        )

    # ---------- Validators ----------

    @field_validator("orchestrator_url", "ollama_host", "admin_url", "public_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"URL must start with http:// or https://, got: {v!r}")
        return v

    @field_validator("state_dir", "output_root", "logs_base", mode="before")
    @classmethod
    def _normalize_path(cls, v: Path | str | None) -> Path | None:
        if v is None or v == "":
            return v
        p = Path(str(v)).expanduser()
        if not p.is_absolute():
            p = REPO_ROOT / p
        return p

    @field_validator("logs_base", mode="after")
    @classmethod
    def _resolve_logs_base_default(cls, v: Path | None) -> Path:
        if v is not None:
            return v
        if os.name == "nt":
            return Path.home() / "data" / "src" / "qap" / "quiz-pipeline-logs"
        return REPO_ROOT / "state" / "logs"

    @model_validator(mode="after")
    def _resolve_session_secret(self) -> "KnowSettings":
        effective = self.effective_env

        if effective == "prod":
            env_secret = os.environ.get("KNOW_SESSION_SECRET") or os.environ.get("SESSION_SECRET")
            if not env_secret:
                raise ValueError(
                    "KNOW_SESSION_SECRET must be set when DEPLOYMENT_ENV=prod. "
                    "Refusing to start with dev-default or file-based session secret."
                )
            self.session_secret = SecretStr(env_secret)
            return self

        # Dev/staging/docker/k8s path
        if self.session_secret is not None:
            if not os.environ.get("KNOW_SESSION_SECRET") and not os.environ.get("SESSION_SECRET"):
                if _yaml_has_session_secret():
                    log.warning(
                        f"session_secret_from_yaml: secret loaded from {_yaml_config_path()}. "
                        f"Env var KNOW_SESSION_SECRET recommended for prod; YAML-stored "
                        f"secrets are vulnerable to accidental commit and backup leaks.",
                        extra={
                            "file": str(_yaml_config_path()),
                            "hint": "KNOW_SESSION_SECRET env var recommended for prod",
                        },
                    )
            return self

        self.session_secret = SecretStr(_load_or_generate_dev_secret(self.state_dir))
        return self

    @model_validator(mode="after")
    def _warn_version_mismatch(self) -> "KnowSettings":
        if self.know_config_version != 1:
            log.warning(
                f"config_version_newer_than_supported: file declares version={self.know_config_version}, "
                f"this build supports version=1. Attempting to load; unknown fields will warn individually.",
                extra={
                    "file_version": self.know_config_version,
                    "supported": 1,
                    "attempted_load": True,
                },
            )
        return self

    @model_validator(mode="after")
    def _generate_scaffold_if_absent(self) -> "KnowSettings":
        if self.deployment_env in ("docker", "k8s"):
            return self

        if _env_override_present():
            log.info("config_scaffold_skipped", extra={"reason": "env_override"})
            return self

        target = _yaml_config_path()
        if target.exists():
            log.info("config_loaded", extra={"path": str(target)})
            return self

        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            template = _load_scaffold_template()
            _atomic_write_if_absent(target, template)
            log.info("config_scaffold_generated", extra={"path": str(target)})
        except OSError as e:
            log.warning(
                f"config_scaffold_failed: could not write {target}: {e}. "
                f"Check directory permissions; pipeline will use env+defaults.",
                extra={"path": str(target), "error": str(e)},
            )
        return self

    # ---------- Derived ----------

    @property
    def effective_env(self) -> str:
        """Deployment env after fallback for unimplemented values. S1.5 only
        implements dev/prod; others behave like dev with a warning."""
        if self.deployment_env in _IMPLEMENTED_ENVS:
            return self.deployment_env
        log.warning(
            f"deployment_env_not_implemented: deployment_env='{self.deployment_env}', "
            f"this build treats it as 'dev'. Set KNOW_SESSION_SECRET explicitly for prod-like strictness.",
            extra={
                "value": self.deployment_env,
                "effective": "dev",
                "hint": "Set KNOW_SESSION_SECRET explicitly for prod-like strictness",
            },
        )
        return "dev"

    @property
    def is_prod(self) -> bool:
        return self.effective_env == "prod"

    @property
    def logs_root(self) -> Path:
        assert self.logs_base is not None
        return self.logs_base / "runs"

    @property
    def acknowledged_path(self) -> Path:
        assert self.logs_base is not None
        return self.logs_base / "acknowledged.jsonl"

    def validate_for_deployment(self) -> None:
        """Sanity checks after resolution. Currently only checks
        state_dir is usable. Called from web.app startup."""
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(f"state_dir not writable: {self.state_dir} ({e})")


def _load_or_generate_dev_secret(state_dir: Path) -> str:
    """Load state/.session_secret if present; else generate atomically.
    Atomic via O_CREAT|O_EXCL -- if a sibling process races us, we lose
    the create, then read what it wrote."""
    path = state_dir / ".session_secret"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    state_dir.mkdir(parents=True, exist_ok=True)
    new = secrets.token_urlsafe(48)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, new.encode("utf-8"))
        finally:
            os.close(fd)
        log.info("session_secret_auto_generated", extra={"path": str(path)})
        _warn_if_state_tracked(path)
        return new
    except FileExistsError:
        # Another process won; use its value
        return path.read_text(encoding="utf-8").strip()


def _warn_if_state_tracked(secret_path: Path) -> None:
    """Heuristic: if state/ appears to be under git control, warn user that
    state/.session_secret may leak into repo. Best-effort, silent on error."""
    try:
        parent = secret_path.parent
        if (parent / ".git").exists() or (parent.parent / ".git").exists():
            # Check if state/ dir itself is tracked -- heuristic only. We
            # cannot reliably detect without invoking git; log a caution.
            log.warning(
                f"session_secret_persisted: wrote {secret_path}. "
                f"Ensure 'state/' or 'state/*' is in .gitignore to prevent accidental commit.",
                extra={
                    "path": str(secret_path),
                    "hint": "Add state/ or state/* to .gitignore",
                },
            )
    except Exception:
        pass


_ENV_OVERRIDE_VARS = frozenset({
    "ORCHESTRATOR_URL", "OLLAMA_HOST", "ADMIN_URL", "PUBLIC_URL",
    "KNOW_STATE_DIR", "KNOW_OUTPUT_ROOT", "KNOW_LOGS_ROOT",
    "KNOW_SESSION_SECRET", "DEPLOYMENT_ENV", "KNOW_CONFIG_VERSION",
})


def _env_override_present() -> bool:
    return any(k in os.environ for k in _ENV_OVERRIDE_VARS)


def _load_scaffold_template() -> str:
    example = _yaml_example_path()
    if example.exists():
        return example.read_text(encoding="utf-8")
    log.warning("config_example_absent_using_minimal", extra={"path": str(example)})
    return "know_config_version: 1\ndeployment_env: dev\n"


def _atomic_write_if_absent(path: Path, content: str) -> bool:
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    return True


@lru_cache(maxsize=1)
def get_settings() -> KnowSettings:
    return KnowSettings()


def reset_settings_cache() -> None:
    """Test helper -- clears the lru_cache so next get_settings()
    reloads from env. Not for production use."""
    get_settings.cache_clear()
