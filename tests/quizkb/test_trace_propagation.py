import os
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from quizkb.observability import extract_traceparent, parse_traceparent
from orchestrator.tasks import TaskManager


def test_extract_traceparent_roundtrip(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    trace._TRACER_PROVIDER = None
    provider = TracerProvider()
    trace.set_tracer_provider(provider)
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("roundtrip_span") as span:
        ctx = span.get_span_context()
        tp = extract_traceparent()
        assert tp is not None
        parsed = parse_traceparent(tp)
        assert parsed is not None
        assert parsed.trace_id == ctx.trace_id
        assert parsed.span_id == ctx.span_id


def test_extract_traceparent_no_context():
    assert extract_traceparent() is None


def test_parse_traceparent_malformed():
    assert parse_traceparent("") is None
    assert parse_traceparent("garbage") is None
    assert parse_traceparent("00-abc-def-01") is None
    assert parse_traceparent("00-00000000000000000000000000000000-0000000000000000-01") is None
    assert parse_traceparent("01-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01") is None


def test_parse_traceparent_flags_not_sampled():
    tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00"
    ctx = parse_traceparent(tp)
    assert ctx is not None
    assert not ctx.trace_flags.sampled


def test_enqueue_stores_trace_parent(monkeypatch, tmp_path):
    test_db = tmp_path / "tasks.db"
    monkeypatch.setattr("orchestrator.tasks.DB_PATH", test_db)
    monkeypatch.setattr("orchestrator.tasks.TASKS_DIR", tmp_path / "tasks")

    tm = TaskManager()
    tp_val = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    tid = tm.enqueue("generate", "linux", "en", trace_parent=tp_val)
    task = tm.get_task(tid)
    assert task["trace_parent"] == tp_val


def test_enqueue_stores_null_trace_parent(monkeypatch, tmp_path):
    test_db = tmp_path / "tasks.db"
    monkeypatch.setattr("orchestrator.tasks.DB_PATH", test_db)
    monkeypatch.setattr("orchestrator.tasks.TASKS_DIR", tmp_path / "tasks")

    tm = TaskManager()
    tid = tm.enqueue("generate", "linux", "en", trace_parent=None)
    task = tm.get_task(tid)
    assert task.get("trace_parent") is None


def test_restart_forwards_trace_parent(monkeypatch, tmp_path):
    test_db = tmp_path / "tasks.db"
    monkeypatch.setattr("orchestrator.tasks.DB_PATH", test_db)
    monkeypatch.setattr("orchestrator.tasks.TASKS_DIR", tmp_path / "tasks")

    tm = TaskManager()
    tp_val = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    tid = tm.enqueue("generate", "linux", "en", trace_parent=tp_val)
    new_id = tm.restart(tid, trace_parent=tp_val)
    task = tm.get_task(new_id)
    assert task["trace_parent"] == tp_val


def test_parse_traceparent_env_var():
    tp_val = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    os.environ["OTEL_TRACEPARENT"] = tp_val
    parsed = parse_traceparent(os.environ["OTEL_TRACEPARENT"])
    assert parsed is not None
    assert parsed.is_remote
    os.environ.pop("OTEL_TRACEPARENT", None)
