"""Stage 0 verification: emit one trace + one metric + one log with shared trace_id.

Install once:
    pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp requests
"""
import logging
import time

from opentelemetry import metrics, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter

OTLP_ENDPOINT = "http://127.0.0.1:4317"
GRAFANA_URL  = "http://127.0.0.1:3000"

resource = Resource.create({"service.name": "test-telemetry", "service.namespace": "know"})

tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer("test")

meter_provider = MeterProvider(
    resource=resource,
    metric_readers=[
        PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=OTLP_ENDPOINT, insecure=True),
            export_interval_millis=2000,
        )
    ],
)
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter("test")
counter = meter.create_counter("test_counter_total", description="Stage 0 test counter")

logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
logging.basicConfig(
    level=logging.INFO,
    handlers=[LoggingHandler(level=logging.INFO, logger_provider=logger_provider)],
)
test_log = logging.getLogger("test.telemetry")

with tracer.start_as_current_span("test_hello") as span:
    span.set_attribute("test.attribute", "stage0")
    trace_id_hex = format(span.get_span_context().trace_id, "032x")
    counter.add(1, {"test_label": "stage0"})
    test_log.info("Stage 0 test log", extra={"trace_id": trace_id_hex})
    print(f"Emitted trace_id = {trace_id_hex}")

tracer_provider.force_flush()
meter_provider.force_flush()
logger_provider.force_flush()

print("\nWaiting 15s for ingestion (Prometheus scrape is 5s)...")
time.sleep(15)

print(f"\nVerify at {GRAFANA_URL}")
print(f"  - Explore -> Tempo -> search trace_id = {trace_id_hex}")
print(f"  - Explore -> Prometheus -> query: test_counter_total")
print(f"  - Explore -> Loki -> query: {{service_name=\"test-telemetry\"}}")
print(f"\nIf all three return data: Stage 0 is PASS.")