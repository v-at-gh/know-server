import pytest
import sqlite3
import sqlite_vec
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from quizkb import db as kb_db
from quizkb.client import EmbedRecord
from scripts import reindex_vec
import quizkb.observability as obs
from tests._metric_helpers import find_metric_value, find_metric_datapoints


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    conn.commit()
    conn.close()
    return db_path


def test_missing_success_emits_run_completed(temp_db, monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(obs, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(reindex_vec, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(reindex_vec, "DB_PATH", temp_db)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing"])
    monkeypatch.setattr(reindex_vec.OllamaClient, "embed_batch", lambda self, texts: EmbedRecord(embeddings=[[0.0]*128], prompt_tokens=1, latency_ms=1, created_at="2026-10-28T00:00:00", error=None))

    with pytest.raises(SystemExit) as exc_info:
        reindex_vec.main()
    assert exc_info.value.code == 0

    assert find_metric_value(reader, "reindex_run_completed_total",
                             {"mode": "missing", "status": "ok"}) == 1
    last_ts = find_metric_value(reader, "reindex_last_success_timestamp_seconds",
                                {"mode": "missing"})
    assert last_ts is not None and last_ts > 0
    assert find_metric_value(reader, "reindex_progress_ratio",
                             {"mode": "missing"}) == 1.0
    # empty DB -> coverage guarded out
    assert find_metric_datapoints(reader, "reindex_current_coverage") == []


def test_dry_run_emits_nothing(temp_db, monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(obs, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(reindex_vec, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(reindex_vec, "DB_PATH", temp_db)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing", "--dry-run"])

    with pytest.raises(SystemExit) as exc_info:
        reindex_vec.main()
    assert exc_info.value.code == 0

    assert find_metric_datapoints(reader, "reindex_run_completed_total") == []
    assert find_metric_datapoints(reader, "reindex_last_success_timestamp_seconds") == []
    assert find_metric_datapoints(reader, "reindex_progress_ratio") == []
    assert find_metric_datapoints(reader, "reindex_current_coverage") == []


def test_prune_does_not_emit_last_success(temp_db, monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(obs, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(reindex_vec, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(reindex_vec, "DB_PATH", temp_db)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--prune"])

    with pytest.raises(SystemExit) as exc_info:
        reindex_vec.main()
    assert exc_info.value.code == 0

    assert find_metric_value(reader, "reindex_run_completed_total",
                             {"mode": "prune", "status": "ok"}) == 1
    assert find_metric_datapoints(reader, "reindex_last_success_timestamp_seconds") == []
    assert find_metric_datapoints(reader, "reindex_progress_ratio") == []


def test_healthcheck_fail_emits_run_completed_failed(temp_db, monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(obs, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(reindex_vec, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(reindex_vec, "DB_PATH", temp_db)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing"])
    monkeypatch.setattr(reindex_vec.OllamaClient, "embed_batch", lambda self, texts: EmbedRecord(embeddings=[], prompt_tokens=0, latency_ms=0, created_at="2026-10-28T00:00:00", error="connection refused"))

    with pytest.raises(SystemExit) as exc_info:
        reindex_vec.main()
    assert exc_info.value.code == 1

    assert find_metric_value(reader, "reindex_run_completed_total",
                             {"mode": "missing", "status": "failed"}) == 1
    assert find_metric_datapoints(reader, "reindex_last_success_timestamp_seconds") == []
