"""Unit tests for ConsoleFormatter and _json_formatter traceback inclusion."""

import json
import logging
import sys
from quizkb.observability import configure_logging


def test_console_formatter_includes_traceback(tmp_path):
    """Console handler formats exception traceback when exc_info is present."""
    log_dir = tmp_path / "logs"
    configure_logging("test_console_service", log_dir=log_dir, level=logging.INFO)

    root = logging.getLogger()
    console_handler = next(h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.handlers.RotatingFileHandler))
    formatter = console_handler.formatter

    try:
        raise ValueError("synthetic_console_error")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test_logger",
        level=logging.ERROR,
        pathname=__file__,
        lineno=10,
        msg="synthetic_error_event",
        args=(),
        exc_info=exc_info,
    )
    output = formatter.format(record)

    assert "synthetic_error_event" in output
    assert "ValueError: synthetic_console_error" in output
    assert "Traceback (most recent call last)" in output


def test_json_formatter_includes_traceback(tmp_path):
    """configure_logging outputs traceback field in jsonl on log.exception()."""
    log_dir = tmp_path / "logs"
    configure_logging("test_json_service", log_dir=log_dir, level=logging.INFO)
    logger = logging.getLogger("test_json_service")

    try:
        raise RuntimeError("synthetic_json_error")
    except RuntimeError:
        logger.exception("synthetic_exception_logged")

    jsonl_file = log_dir / "test_json_service.jsonl"
    assert jsonl_file.exists()

    lines = [line for line in jsonl_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) > 0

    exc_entry = next(json.loads(line) for line in lines if json.loads(line).get("event") == "synthetic_exception_logged")
    assert "traceback" in exc_entry
    assert "RuntimeError: synthetic_json_error" in exc_entry["traceback"]
    assert "Traceback" in exc_entry["traceback"]
