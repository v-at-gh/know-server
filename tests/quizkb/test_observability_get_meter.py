from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from quizkb.observability import get_meter


def test_get_meter_no_provider_returns_noop():
    meter = get_meter("test_noop")
    assert meter is not None
    counter = meter.create_counter("test_counter")
    counter.add(1)


def test_get_meter_with_provider(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = get_meter("test_real", provider=provider)
    counter = meter.create_counter("test_real_counter")
    counter.add(42, {"key": "val"})
    metrics_data = reader.get_metrics_data()
    assert metrics_data is not None
