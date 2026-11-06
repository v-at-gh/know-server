"""Tests for worker_active_tasks gauge emission."""

import pytest
import opentelemetry.metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import scripts.worker
from tests._metric_helpers import find_metric_value


@pytest.fixture
def gauge(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    opentelemetry.metrics.set_meter_provider(provider)
    test_gauge = provider.get_meter("worker").create_gauge("worker_active_tasks")
    return test_gauge, reader


def test_emit_active_tasks_idle(gauge):
    test_gauge, reader = gauge
    scripts.worker._emit_active_tasks(None, gauge=test_gauge)
    assert find_metric_value(reader, "worker_active_tasks", {"kind": "generation"}) == 0
    assert find_metric_value(reader, "worker_active_tasks", {"kind": "reindex"}) == 0


def test_emit_active_tasks_generation(gauge):
    test_gauge, reader = gauge
    scripts.worker._emit_active_tasks("generation", gauge=test_gauge)
    assert find_metric_value(reader, "worker_active_tasks", {"kind": "generation"}) == 1
    assert find_metric_value(reader, "worker_active_tasks", {"kind": "reindex"}) == 0


def test_emit_active_tasks_reindex(gauge):
    test_gauge, reader = gauge
    scripts.worker._emit_active_tasks("reindex", gauge=test_gauge)
    assert find_metric_value(reader, "worker_active_tasks", {"kind": "generation"}) == 0
    assert find_metric_value(reader, "worker_active_tasks", {"kind": "reindex"}) == 1
