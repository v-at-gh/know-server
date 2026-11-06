"""Test _OtelAttributeSanitizer converts non-primitive record attributes."""

import logging
import pytest


def _make_record(**extras) -> logging.LogRecord:
    rec = logging.LogRecord(
        name="test", level=logging.INFO,
        pathname="x", lineno=1, msg="msg", args=(), exc_info=None,
    )
    for k, v in extras.items():
        setattr(rec, k, v)
    return rec


def test_primitive_attributes_unchanged():
    from quizkb.observability import _OtelAttributeSanitizer
    f = _OtelAttributeSanitizer()
    rec = _make_record(custom_str="abc", custom_int=42, custom_list=[1, 2],
                      custom_dict={"a": 1}, custom_none=None)
    f.filter(rec)
    assert rec.custom_str == "abc"
    assert rec.custom_int == 42
    assert rec.custom_list == [1, 2]
    assert rec.custom_dict == {"a": 1}
    assert rec.custom_none is None


def test_logger_attribute_converted_to_str():
    """Reproduces the _FixedFindCallerLogger case: an attribute carries a
    logger instance, which is not a primitive."""
    from quizkb.observability import _OtelAttributeSanitizer
    f = _OtelAttributeSanitizer()
    inner_logger = logging.getLogger("some.other.logger")
    rec = _make_record(logger=inner_logger)
    f.filter(rec)
    assert isinstance(rec.logger, str)
    assert "Logger" in rec.logger


def test_custom_object_converted_to_str():
    class Custom:
        def __str__(self):
            return "custom-repr"
    from quizkb.observability import _OtelAttributeSanitizer
    f = _OtelAttributeSanitizer()
    rec = _make_record(obj=Custom())
    f.filter(rec)
    assert rec.obj == "custom-repr"


def test_unserializable_object_converted_to_repr():
    class Bad:
        def __str__(self):
            raise RuntimeError("nope")
    from quizkb.observability import _OtelAttributeSanitizer
    f = _OtelAttributeSanitizer()
    rec = _make_record(obj=Bad())
    f.filter(rec)
    assert isinstance(rec.obj, str)  # via repr fallback


def test_filter_returns_true_always():
    """Filter should never drop records."""
    from quizkb.observability import _OtelAttributeSanitizer
    f = _OtelAttributeSanitizer()
    assert f.filter(_make_record()) is True
