import pytest
from quizkb.observability import safe_exit
import quizkb.observability as obs


def test_safe_exit_flushes(monkeypatch):
    flushed = []
    monkeypatch.setattr(obs, "force_flush_telemetry", lambda: flushed.append(True))
    with pytest.raises(SystemExit) as exc_info:
        safe_exit(0)
    assert exc_info.value.code == 0
    assert len(flushed) == 1


def test_safe_exit_no_providers_safe(monkeypatch):
    monkeypatch.setattr(obs, "force_flush_telemetry", lambda: (_ for _ in ()).throw(Exception("no provider")))
    with pytest.raises(SystemExit) as exc_info:
        safe_exit(5)
    assert exc_info.value.code == 5


def test_safe_exit_code_preserved():
    with pytest.raises(SystemExit) as exc_info:
        safe_exit(130)
    assert exc_info.value.code == 130
