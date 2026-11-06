import os
import logging
import json
import pytest
from unittest.mock import MagicMock, patch
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from quizkb import observability
from quizkb.observability import configure_telemetry, configure_logging
from quizkb.client import OllamaClient


def test_configure_telemetry_disabled(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    from quizkb import observability
    observability._telemetry_configured.discard("test_svc_disabled")

    configure_telemetry("test_svc_disabled")
    assert "test_svc_disabled" in observability._telemetry_configured


def test_configure_telemetry_idempotent(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    configure_telemetry("test_svc_idem")
    configure_telemetry("test_svc_idem")


def test_trace_id_populated_in_log_record(tmp_path, monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace._TRACER_PROVIDER = provider

    configure_logging("test_trace_log", log_dir=tmp_path)
    tracer = trace.get_tracer("test")

    with tracer.start_as_current_span("test_span") as span:
        trace_id_hex = format(span.get_span_context().trace_id, "032x")
        raw_log = logging.getLogger("test_trace")
        raw_log.info("inside span log", extra={"otelTraceID": span.get_span_context().trace_id, "otelSpanID": span.get_span_context().span_id})

    for h in logging.getLogger().handlers:
        h.flush()

    jsonl_file = tmp_path / "test_trace_log.jsonl"
    lines = jsonl_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[-1])
    assert record["trace_id"] == trace_id_hex


def test_trace_id_null_outside_span(tmp_path):
    configure_logging("test_null_trace", log_dir=tmp_path)
    raw_log = logging.getLogger("test_null")
    raw_log.info("outside span log")

    for h in logging.getLogger().handlers:
        h.flush()

    jsonl_file = tmp_path / "test_null_trace.jsonl"
    lines = jsonl_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[-1])
    assert record["trace_id"] is None


def test_ollama_client_creates_span(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    provider = TracerProvider()
    trace._TRACER_PROVIDER = provider
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    with patch("quizkb.client._OllamaClient") as mock_ollama_cls:
        mock_instance = MagicMock()
        mock_instance.generate.return_value = {
            "response": "Hello world",
            "prompt_eval_count": 10,
            "eval_count": 20,
        }
        mock_ollama_cls.return_value = mock_instance

        client = OllamaClient()
        rec = client.query("Tell me something", predict=64)

        spans = exporter.get_finished_spans()
        llm_spans = [s for s in spans if s.name == "llm.generate"]
        assert len(llm_spans) >= 1
        s = llm_spans[-1]
        assert s.attributes["llm.predict"] == 64
        assert s.attributes["llm.tokens_in"] == 10
        assert s.attributes["llm.tokens_out"] == 20


def test_force_flush_telemetry():
    from quizkb.observability import force_flush_telemetry, _providers
    mock_provider = MagicMock()
    _providers["test_p"] = mock_provider
    try:
        force_flush_telemetry()
        mock_provider.force_flush.assert_called_once_with(timeout_millis=2000)
    finally:
        _providers.pop("test_p", None)


@pytest.fixture(autouse=True)
def _isolate_telemetry_state():
    """Reset module-level telemetry between tests, shutdown providers to
    stop gRPC daemon retry threads."""
    saved_providers = dict(observability._providers)
    saved_configured = set(observability._telemetry_configured)
    yield
    for provider in observability._providers.values():
        try:
            provider.shutdown()
        except Exception:
            pass
    observability._providers.clear()
    observability._providers.update(saved_providers)
    observability._telemetry_configured.clear()
    observability._telemetry_configured.update(saved_configured)