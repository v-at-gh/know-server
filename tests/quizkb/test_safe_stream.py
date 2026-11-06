"""Unit tests for SafeStream wrapper."""

import pytest
from quizkb.io_utils import SafeStream


class BrokenStream:
    """Mock that raises on write."""
    def write(self, s):
        raise OSError(22, "Invalid argument")
    def flush(self):
        raise OSError(22, "Invalid argument")


class HealthyStream:
    def __init__(self):
        self.written = []
    def write(self, s):
        self.written.append(s)
        return len(s)
    def flush(self):
        pass


def test_passthrough_when_healthy():
    inner = HealthyStream()
    s = SafeStream(inner)
    s.write("hello")
    s.flush()
    assert inner.written == ["hello"]
    assert s._broken is False


def test_swallows_oserror_on_write():
    s = SafeStream(BrokenStream())
    # Must not raise
    result = s.write("hello")
    assert s._broken is True
    # Returns length so caller thinks write succeeded
    assert result == len("hello")


def test_swallows_oserror_on_flush():
    s = SafeStream(BrokenStream())
    s.write("x")  # breaks
    # flush after broken must not raise
    s.flush()
    assert s._broken is True


def test_skips_write_after_broken():
    s = SafeStream(BrokenStream())
    s.write("first")  # breaks
    # Subsequent writes silently no-op
    s.write("second")
    s.write("third")
    assert s._broken is True


def test_attribute_passthrough():
    inner = HealthyStream()
    inner.encoding = "utf-8"
    s = SafeStream(inner)
    assert s.encoding == "utf-8"


def test_install_idempotent(monkeypatch):
    import sys
    from quizkb.io_utils import install_safe_std_streams
    original_out = sys.stdout
    original_err = sys.stderr
    try:
        install_safe_std_streams()
        assert isinstance(sys.stdout, SafeStream)
        install_safe_std_streams()  # second call must not double-wrap
        assert isinstance(sys.stdout, SafeStream)
        assert not isinstance(sys.stdout._wrapped, SafeStream)
    finally:
        sys.stdout = original_out
        sys.stderr = original_err
