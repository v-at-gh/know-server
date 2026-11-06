import importlib
import pytest
from pathlib import Path
from quizkb.settings import reset_settings_cache

def test_logs_root_is_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOW_LOGS_ROOT", str(tmp_path / "logs"))
    reset_settings_cache()
    from quizkb import config
    importlib.reload(config)
    assert config.LOGS_ROOT.is_absolute()
    assert config.LOGS_ROOT == tmp_path / "logs" / "runs"
    assert config.ACKNOWLEDGED_PATH == tmp_path / "logs" / "acknowledged.jsonl"

def test_logs_root_fallback_when_win_path_not_absolute(monkeypatch):
    monkeypatch.delenv("KNOW_LOGS_ROOT", raising=False)
    reset_settings_cache()
    from quizkb import config
    importlib.reload(config)
    assert config.LOGS_ROOT.is_absolute()
