import logging
import json
from pathlib import Path
import pytest
import structlog.contextvars
from quizkb.observability import configure_logging

def test_configure_logging_creates_file(tmp_path):
    configure_logging("test-service", log_dir=tmp_path)
    logger = logging.getLogger("test")
    logger.info("hello world")

    for h in logging.getLogger().handlers:
        h.flush()

    jsonl_file = tmp_path / "test-service.jsonl"
    assert jsonl_file.exists()
    lines = jsonl_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[-1])
    required_keys = {"ts", "level", "service", "logger", "event", "task_id", "trace_id", "span_id"}
    assert required_keys.issubset(record.keys())
    assert record["event"] == "hello world"
    assert record["service"] == "test-service"

def test_configure_logging_idempotent(tmp_path):
    configure_logging("test-service", log_dir=tmp_path)
    configure_logging("test-service", log_dir=tmp_path)
    logger = logging.getLogger("test")
    logger.info("idempotent test")

    for h in logging.getLogger().handlers:
        h.flush()

    jsonl_file = tmp_path / "test-service.jsonl"
    lines = jsonl_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

def test_configure_logging_task_id_from_extra(tmp_path):
    configure_logging("test-service", log_dir=tmp_path)
    raw_logger = logging.getLogger("worker")
    adapter = logging.LoggerAdapter(raw_logger, {"task_id": "task-xyz-123"})
    adapter.info("task action performed")

    for h in logging.getLogger().handlers:
        h.flush()

    jsonl_file = tmp_path / "test-service.jsonl"
    lines = jsonl_file.read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[-1])
    assert record["task_id"] == "task-xyz-123"

def test_configure_logging_trace_id_null_in_stage_3a(tmp_path):
    configure_logging("test-service", log_dir=tmp_path)
    logger = logging.getLogger("test")
    logger.info("stage 3a trace check")

    for h in logging.getLogger().handlers:
        h.flush()

    jsonl_file = tmp_path / "test-service.jsonl"
    lines = jsonl_file.read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[-1])
    assert record["trace_id"] is None
    assert record["span_id"] is None

def test_configure_logging_console_human_format(tmp_path, capsys):
    configure_logging("test-service", log_dir=tmp_path)
    logger = logging.getLogger("test")
    logger.info("console human check")

    for h in logging.getLogger().handlers:
        h.flush()

    captured = capsys.readouterr()
    stderr = captured.err
    assert stderr.strip() != ""
    assert not stderr.strip().startswith("{")
    assert "console human check" in stderr

def test_bind_contextvars_task_id_in_subprocess(tmp_path):
    configure_logging("subprocess", log_dir=tmp_path)
    structlog.contextvars.bind_contextvars(task_id="abc123")
    logger = logging.getLogger("subprocess_test")
    logger.info("subprocess log")

    for h in logging.getLogger().handlers:
        h.flush()

    jsonl_file = tmp_path / "subprocess.jsonl"
    lines = jsonl_file.read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[-1])
    assert record["task_id"] == "abc123"
    structlog.contextvars.clear_contextvars()

def test_unbound_contextvars_task_id_is_null(tmp_path):
    configure_logging("subprocess", log_dir=tmp_path)
    structlog.contextvars.clear_contextvars()
    logger = logging.getLogger("subprocess_test")
    logger.info("unbound log")

    for h in logging.getLogger().handlers:
        h.flush()

    jsonl_file = tmp_path / "subprocess.jsonl"
    lines = jsonl_file.read_text(encoding="utf-8").strip().splitlines()
    record = json.loads(lines[-1])
    assert record["task_id"] is None
