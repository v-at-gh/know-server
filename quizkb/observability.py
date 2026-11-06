from __future__ import annotations

import logging
import logging.handlers
import sys
import datetime
import os
from pathlib import Path
import structlog
from structlog.stdlib import ProcessorFormatter
from typing import Any

# OTel Imports
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

_telemetry_configured: set[str] = set()
_providers: dict[str, Any] = {}

_STD_LOGRECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
)
_INTERNAL_OTEL_ATTRS = frozenset({
    "otelTraceID", "otelSpanID", "otelTraceSampled", "otelServiceName",
})


def extract_traceparent() -> str | None:
    """Format current span context as W3C traceparent. Returns None if no
    active, valid, sampled-or-not span context."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None
    flags = "01" if ctx.trace_flags.sampled else "00"
    return f"00-{trace.format_trace_id(ctx.trace_id)}-{trace.format_span_id(ctx.span_id)}-{flags}"


def parse_traceparent(tp: str) -> trace.SpanContext | None:
    """Parse W3C traceparent string. Returns None on malformed input or
    all-zero IDs."""
    if not tp or not isinstance(tp, str):
        return None
    parts = tp.strip().split("-")
    if len(parts) != 4 or parts[0] != "00":
        return None
    t_str, s_str, f_str = parts[1], parts[2], parts[3]
    if len(t_str) != 32 or len(s_str) != 16 or len(f_str) != 2:
        return None
    try:
        trace_id = int(t_str, 16)
        span_id = int(s_str, 16)
        flags = int(f_str, 16)
    except (ValueError, TypeError):
        return None
    if trace_id == 0 or span_id == 0:
        return None
    return trace.SpanContext(
        trace_id=trace_id,
        span_id=span_id,
        is_remote=True,
        trace_flags=trace.TraceFlags(flags),
    )


class _OtelAttributeSanitizer(logging.Filter):
    """Normalize non-primitive record attributes to str before OTel
    LoggingHandler attempts to serialize them as span attributes.

    OTel SDK's attribute serializer rejects non-primitive types (loggers,
    custom objects, functions) with a warning. Structlog wraps stdlib
    Logger instances under some attributes (e.g. `_FixedFindCallerLogger`)
    which triggers warnings on every structlog log emission. This filter
    runs before LoggingHandler.emit() and converts offending values to
    their string representation. Original semantic preserved for other
    handlers (which don't serialize to OTel).
    """

    _PRIMITIVE = (str, int, float, bool, type(None), bytes, dict, list, tuple)

    def filter(self, record: logging.LogRecord) -> bool:
        for k in list(record.__dict__.keys()):
            v = record.__dict__[k]
            if isinstance(v, self._PRIMITIVE):
                continue
            try:
                record.__dict__[k] = str(v)
            except Exception:
                try:
                    record.__dict__[k] = repr(v)
                except Exception:
                    record.__dict__[k] = "<unserializable>"
        return True


def configure_telemetry(
    service_name: str,
    *,
    endpoint: str | None = None,
    sampler_ratio: float = 1.0,
) -> None:
    """Initialize TracerProvider, MeterProvider, and instrumentation.

    Idempotent. Safe to call multiple times. No-op if OTEL_SDK_DISABLED=true.
    Reads OTEL_EXPORTER_OTLP_ENDPOINT env var if endpoint is None.
    """
    if os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        logging.getLogger(__name__).info("telemetry_disabled", extra={"service": service_name})
        _telemetry_configured.add(service_name)
        return

    if service_name in _telemetry_configured:
        return

    otlp_endpoint = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not otlp_endpoint:
        logging.getLogger(__name__).info("telemetry_no_endpoint", extra={"service": service_name})
        return

    env = os.environ.get("DEPLOYMENT_ENV", "dev")

    resource = Resource.create({
        "service.name": service_name,
        "service.namespace": "know",
        "deployment.environment": env,
    })

    tracer_provider = None
    meter_provider = None
    logger_provider = None

    # TracerProvider
    tracer_provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(sampler_ratio)),
    )
    try:
        span_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    except Exception as e:
        logging.getLogger(__name__).warning("Failed to initialize OTLPSpanExporter: %s", e)
    trace.set_tracer_provider(tracer_provider)

    # MeterProvider
    try:
        metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True)
        reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=5000)
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(meter_provider)
    except Exception as e:
        logging.getLogger(__name__).warning("Failed to initialize OTLPMetricExporter: %s", e)

    # LoggerProvider
    try:
        log_exporter = OTLPLogExporter(endpoint=otlp_endpoint, insecure=True)
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

        from opentelemetry.sdk._logs import LoggingHandler
        # Create handler with the provider we just created
        log_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
        log_handler.addFilter(_OtelAttributeSanitizer())
        # Attach to the root logger so all standard logging calls are captured
        logging.getLogger().addHandler(log_handler)
        logging.getLogger(__name__).info("otel_logging_handler_attached")
    except Exception as e:
        logging.getLogger(__name__).warning("failed_to_attach_logging_handler: %s", e)

    # Instrumentations
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
        logging.getLogger(__name__).info("otel_requests_instrumented")
    except Exception as e:
        logging.getLogger(__name__).warning("failed_otel_requests: %s", e)

    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        LoggingInstrumentor().instrument(set_logging_format=False)
        logging.getLogger(__name__).info("otel_logging_instrumented")
    except Exception as e:
        logging.getLogger(__name__).warning("failed_otel_logging: %s", e)

    _telemetry_configured.add(service_name)
    if tracer_provider:
        _providers["tracer"] = tracer_provider
    if meter_provider:
        _providers["meter"] = meter_provider
    if logger_provider:
        _providers["logger"] = logger_provider


def force_flush_telemetry() -> None:
    for p in _providers.values():
        if hasattr(p, "force_flush"):
            try:
                p.force_flush(timeout_millis=2000)
            except Exception:
                pass


def get_meter(name: str = "know", *, provider=None) -> metrics.Meter:
    """Return OTel meter. If `provider` is given, return meter from that
    provider (test-only escape hatch). Otherwise delegate to OTel global
    metrics.get_meter — returns NoOp meter if configure_telemetry did not
    initialize MeterProvider (env unset, SDK disabled, or exception during
    setup). Safe to call at module import time."""
    if provider is not None:
        return provider.get_meter(name)
    return metrics.get_meter(name)


def safe_exit(code: int = 0) -> None:
    """Flush telemetry, then exit. Drop-in replacement for sys.exit() in
    CLI entry points. KeyboardInterrupt callers should use safe_exit(130).
    Idempotent-safe: force_flush_telemetry handles empty _providers."""
    try:
        force_flush_telemetry()
    except Exception:
        pass
    sys.exit(code)


def configure_logging(
    service: str,
    log_dir: Path = Path("state/logs"),
    level: int = logging.INFO,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    try:
        from opentelemetry.sdk._logs import LoggingHandler
        handlers_to_remove = [h for h in root_logger.handlers if not isinstance(h, LoggingHandler)]
    except ImportError:
        handlers_to_remove = list(root_logger.handlers)
    for h in handlers_to_remove:
        root_logger.removeHandler(h)
    root_logger.setLevel(level)

    def extract_record_attrs(logger, method_name, event_dict):
        record = event_dict.get("_record")

        # 1. Copy custom extras (like task_id)
        if record:
            import logging as _logging
            std_attrs = set(vars(_logging.LogRecord("", 0, "", 0, "", (), None)).keys())
            for key, val in vars(record).items():
                if not key.startswith("_") and key not in std_attrs and key not in ("task_id", "trace_id", "span_id"):
                    event_dict.setdefault(key, val)
            if hasattr(record, "task_id") and record.task_id:
                event_dict["task_id"] = record.task_id

        # 2. Extract trace_id directly from current OTel span
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
        else:
            event_dict["trace_id"] = None
            event_dict["span_id"] = None

        return event_dict

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        extract_record_attrs,
    ]

    def add_service(logger, method_name, event_dict):
        event_dict["service"] = service
        return event_dict

    structlog.configure(
        processors=[
            *shared_processors,
            add_service,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    json_renderer = structlog.processors.JSONRenderer(ensure_ascii=False)

    def _json_formatter(logger, method_name, event_dict):
        record = {
            "ts": event_dict.get("timestamp"),
            "level": event_dict.get("level"),
            "service": event_dict.get("service", service),
            "logger": event_dict.get("logger"),
            "event": event_dict.get("event"),
            "task_id": event_dict.get("task_id", None),
            "trace_id": event_dict.get("trace_id", None),
            "span_id": event_dict.get("span_id", None),
        }
        for k, v in event_dict.items():
            if k not in (
                "timestamp", "level", "service", "logger", "event",
                "task_id", "trace_id", "span_id", "_record", "_from_structlog",
                "exc_info", "stack_info", "lineno", "filename", "funcName",
                "process", "thread", "threadName", "processName", "pathname",
                "created", "msecs", "relativeCreated", "levelno", "msg", "args"
            ):
                record[k] = v

        exc_info = event_dict.get("exc_info")
        if exc_info:
            import traceback
            if isinstance(exc_info, tuple):
                record["traceback"] = "".join(traceback.format_exception(*exc_info))
            elif isinstance(exc_info, BaseException):
                record["traceback"] = "".join(
                    traceback.format_exception(type(exc_info), exc_info, exc_info.__traceback__)
                )
            else:
                record["traceback"] = str(exc_info)

        return json_renderer(logger, method_name, record)

    file_formatter = ProcessorFormatter(
        foreign_pre_chain=shared_processors + [add_service],
        processors=[
            ProcessorFormatter.remove_processors_meta,
            _json_formatter,
        ],
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / f"{service}.jsonl",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    class ConsoleFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            task_id = getattr(record, "task_id", None)
            if not task_id:
                try:
                    cv = structlog.contextvars.get_contextvars()
                    task_id = cv.get("task_id", "-")
                except Exception:
                    task_id = "-"
            if not task_id:
                task_id = "-"
            record.task_id = task_id
            ts = datetime.datetime.fromtimestamp(record.created, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            levelname = record.levelname
            name = record.name
            if isinstance(record.msg, dict):
                msg = record.msg.get("event", str(record.msg))
            else:
                msg = record.getMessage()
            line = f"{ts} [{levelname}] {name} [task={task_id}]: {msg}"
            if record.exc_info:
                line += "\n" + self.formatException(record.exc_info)
            return line

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(ConsoleFormatter())
    root_logger.addHandler(console_handler)


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)
