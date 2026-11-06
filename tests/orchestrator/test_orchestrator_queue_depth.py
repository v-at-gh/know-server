import pytest
import asyncio
import importlib
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import orchestrator.app
from orchestrator.app import app
from orchestrator.tasks import TaskManager
import quizkb.observability as obs
from tests._metric_helpers import find_metric_value


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    db_file = tmp_path / "tasks.db"
    monkeypatch.setattr("orchestrator.tasks.DB_PATH", db_file)
    tm = TaskManager()
    app.state.task_manager = tm


@pytest.fixture
def gauges(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(obs, "get_meter", lambda name: provider.get_meter(name))
    importlib.reload(orchestrator.app)
    # `app` in this module refers to the pre-reload instance, which has
    # state.task_manager set by setup_env. Reloaded `orchestrator.app`
    # provides new gauges bound to test provider; its _emit_queue_depth_once
    # reads state from whatever `app` it receives, so old app works.
    return orchestrator.app.queue_depth, reader


def test_queue_depth_emitted_with_two_kinds(gauges):
    test_queue_depth, reader = gauges
    tm = app.state.task_manager
    tm.enqueue(action="bootstrap", domain="linux", lang="en", kind="generation")
    tm.enqueue(action="bootstrap", domain="docker", lang="en", kind="generation")
    tm.enqueue(action="reindex_missing", kind="reindex")

    asyncio.run(orchestrator.app._emit_queue_depth_once(app))

    assert find_metric_value(reader, "orchestrator_queue_depth", {"kind": "generation"}) == 2
    assert find_metric_value(reader, "orchestrator_queue_depth", {"kind": "reindex"}) == 1


def test_queue_depth_explicit_zero(gauges):
    test_queue_depth, reader = gauges
    tm = app.state.task_manager
    tm.enqueue(action="bootstrap", domain="linux", lang="en", kind="generation")

    asyncio.run(orchestrator.app._emit_queue_depth_once(app))

    assert find_metric_value(reader, "orchestrator_queue_depth", {"kind": "generation"}) == 1
    assert find_metric_value(reader, "orchestrator_queue_depth", {"kind": "reindex"}) == 0
