import os
import logging
import json
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from fastapi import FastAPI
from fastapi.testclient import TestClient

from quizkb.observability import configure_logging


def test_fastapi_instrumentor_creates_spans_with_real_provider(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace._TRACER_PROVIDER = provider

    app = FastAPI()
    FastAPIInstrumentor.instrument_app(app)

    @app.get("/probe")
    def probe():
        return {"ok": True}

    with TestClient(app) as client:
        client.get("/probe")

    spans = exporter.get_finished_spans()
    http_spans = [s for s in spans if "probe" in s.name.lower() or "GET" in s.name]
    assert http_spans, f"no HTTP span created; got {[s.name for s in spans]}"


def test_extract_record_attrs_copies_extra_fields(tmp_path):
    configure_logging("test_extra_propagation", log_dir=tmp_path)
    log = logging.getLogger("test_extra_propagation")
    log.info("probe", extra={"provider": "TracerProvider", "phase": "start", "done": 5})

    for h in logging.getLogger().handlers:
        h.flush()

    lines = (tmp_path / "test_extra_propagation.jsonl").read_text().splitlines()
    record = json.loads(lines[-1])
    assert record.get("provider") == "TracerProvider"
    assert record.get("phase") == "start"
    assert record.get("done") == 5


def test_extract_record_attrs_trace_id_null_without_span(tmp_path):
    configure_logging("test_trace_null", log_dir=tmp_path)
    logging.getLogger("test_trace_null").info("no span here")

    for h in logging.getLogger().handlers:
        h.flush()

    lines = (tmp_path / "test_trace_null.jsonl").read_text().splitlines()
    record = json.loads(lines[-1])
    assert "trace_id" in record
    assert record["trace_id"] is None
    assert "span_id" in record
    assert record["span_id"] is None


def test_extract_record_attrs_trace_id_hex_within_span(tmp_path, monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    provider = TracerProvider()
    trace._TRACER_PROVIDER = provider
    tracer = trace.get_tracer("test")

    configure_logging("test_trace_hex", log_dir=tmp_path)
    with tracer.start_as_current_span("probe") as span:
        trace_id_hex = format(span.get_span_context().trace_id, "032x")
        logging.getLogger("test_trace_hex").info("inside span", extra={"otelTraceID": span.get_span_context().trace_id, "otelSpanID": span.get_span_context().span_id})

    for h in logging.getLogger().handlers:
        h.flush()

    lines = (tmp_path / "test_trace_hex.jsonl").read_text().splitlines()
    record = json.loads(lines[-1])
    assert record["trace_id"] == trace_id_hex
    assert len(record["trace_id"]) == 32


def test_logging_handler_forwards_to_otel(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor, LogRecordExporter, LogExportResult

    class InMemoryLogExporter(LogRecordExporter):
        def __init__(self):
            self.finished_log_records = []

        def export(self, batch):
            self.finished_log_records.extend(batch)
            return LogExportResult.SUCCESS

        def force_flush(self, timeout_millis=None):
            return True

        def shutdown(self):
            pass

    exporter = InMemoryLogExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))

    handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
    root = logging.getLogger()
    root.addHandler(handler)

    log = logging.getLogger("test_otel_log")
    log.info("hello from otel handler")

    handler.flush()
    exported = exporter.finished_log_records
    assert len(exported) >= 1
    assert any("hello from otel handler" in str(r.log_record.body) for r in exported)

    root.removeHandler(handler)
