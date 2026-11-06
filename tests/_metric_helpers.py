"""Helpers for asserting OTel metric emission in tests."""
from __future__ import annotations

from opentelemetry.sdk.metrics.export import InMemoryMetricReader


def find_metric_value(reader: InMemoryMetricReader, name: str,
                     labels: dict | None = None) -> float | None:
    """Return the value of the first datapoint matching `name` and all
    key=value pairs in `labels`. None if metric or matching datapoint
    not found."""
    data = reader.get_metrics_data()
    if data is None:
        return None
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name != name:
                    continue
                for dp in m.data.data_points:
                    if labels and not all(
                        dp.attributes.get(k) == v for k, v in labels.items()
                    ):
                        continue
                    return dp.value
    return None


def find_metric_datapoints(reader: InMemoryMetricReader, name: str) -> list[dict]:
    """Return list of {labels: {...}, value: N} for all datapoints of
    metric `name`. Empty list if metric absent."""
    data = reader.get_metrics_data()
    if data is None:
        return []
    out = []
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name != name:
                    continue
                for dp in m.data.data_points:
                    out.append({
                        "labels": dict(dp.attributes),
                        "value": dp.value,
                    })
    return out
