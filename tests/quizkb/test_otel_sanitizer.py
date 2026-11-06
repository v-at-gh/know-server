import logging
from quizkb.observability import _OtelAttributeSanitizer


def test_sanitizer_converts_logger_attr():
    """Attach sanitizer to a LogRecord, verify non-primitive attr becomes str."""
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="",
        lineno=0, msg="test", args=(), exc_info=None,
    )
    record.weird_attr = logging.getLogger("nested")  # non-primitive
    sanitizer = _OtelAttributeSanitizer()
    sanitizer.filter(record)
    assert isinstance(record.weird_attr, str)


def test_sanitizer_preserves_primitives():
    """Verify sanitizer does not corrupt primitive types."""
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="",
        lineno=0, msg="test", args=(), exc_info=None,
    )
    record.int_attr = 42
    record.str_attr = "hello"
    record.dict_attr = {"a": 1}
    sanitizer = _OtelAttributeSanitizer()
    sanitizer.filter(record)
    assert record.int_attr == 42
    assert record.str_attr == "hello"
    assert record.dict_attr == {"a": 1}
