import json
import pytest
import sqlite3
import sqlite_vec
from pathlib import Path
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from quizkb import db as kb_db
from quizkb.db import _classify_embed_error
from quizkb import observability as obs
from scripts import reindex_vec
from tests._metric_helpers import find_metric_value, find_metric_datapoints


@pytest.fixture(autouse=True)
def enable_otel(monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)


def test_db_classifies_connection_vs_timeout():
    assert _classify_embed_error("ConnectionError: failed") == "connection"
    assert _classify_embed_error("connection refused") == "connection"
    assert _classify_embed_error("TimeoutError: 30s") == "timeout"
    assert _classify_embed_error("request timed out") == "timeout"
    assert _classify_embed_error("HTTP 500 error") == "http"
    assert _classify_embed_error("ResponseError: bad") == "http"
    assert _classify_embed_error("status code 404") == "http"
    assert _classify_embed_error("Some random error") == "unknown_embed"
    assert _classify_embed_error(None) == "unknown_embed"


def test_duration_histogram_recorded(tmp_path, monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(obs, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(reindex_vec, "get_meter", lambda name: provider.get_meter(name))

    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.commit()
    conn.close()

    monkeypatch.setattr(reindex_vec, "DB_PATH", db_path)
    monkeypatch.setattr(reindex_vec, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing", "--task-id", "t1"])

    class MockOllamaClient:
        def __init__(self, *args, **kwargs):
            pass
        def embed_batch(self, texts):
            class Res:
                error = None
            return Res()

    monkeypatch.setattr(reindex_vec, "OllamaClient", MockOllamaClient)
    monkeypatch.setattr(reindex_vec, "_ensure_schema", lambda p: None)

    with pytest.raises(SystemExit) as exc_info:
        reindex_vec.main()

    assert exc_info.value.code == 0

    data = reader.get_metrics_data()
    found = False
    if data:
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    if m.name == "reindex_duration_seconds":
                        for dp in m.data.data_points:
                            if dp.count > 0:
                                found = True
    assert found


def test_no_duration_on_dry_run(tmp_path, monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(obs, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(reindex_vec, "get_meter", lambda name: provider.get_meter(name))

    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.commit()
    conn.close()

    monkeypatch.setattr(reindex_vec, "DB_PATH", db_path)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing", "--dry-run"])

    with pytest.raises(SystemExit) as exc_info:
        reindex_vec.main()

    assert exc_info.value.code == 0

    dps = find_metric_datapoints(reader, "reindex_duration_seconds")
    assert dps == []


def test_no_duration_on_healthcheck_fail(tmp_path, monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(obs, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(reindex_vec, "get_meter", lambda name: provider.get_meter(name))

    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.commit()
    conn.close()

    monkeypatch.setattr(reindex_vec, "DB_PATH", db_path)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing"])

    class MockFailClient:
        def __init__(self, *args, **kwargs):
            pass
        def embed_batch(self, texts):
            class Res:
                error = "ConnectionError: refused"
            return Res()

    monkeypatch.setattr(reindex_vec, "OllamaClient", MockFailClient)

    with pytest.raises(SystemExit) as exc_info:
        reindex_vec.main()

    assert exc_info.value.code == 1

    assert find_metric_datapoints(reader, "reindex_duration_seconds") == []
    assert find_metric_value(reader, "reindex_run_completed_total", {"mode": "missing", "status": "failed"}) == 1


def test_error_counter_on_embed_fail(tmp_path, monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(obs, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(reindex_vec, "get_meter", lambda name: provider.get_meter(name))

    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO domains (slug, kind) VALUES ('devops', 'topic')")
    did = cursor.lastrowid
    cursor.execute("INSERT INTO articles (uuid, domain_id, slug) VALUES ('art-1', ?, 'git')", (did,))
    aid = cursor.lastrowid
    cursor.execute("INSERT INTO article_translations (article_id, lang, title, summary, content) VALUES (?, 'en', 'Git', 'S', 'C')", (aid,))
    conn.commit()
    conn.close()

    monkeypatch.setattr(reindex_vec, "DB_PATH", db_path)
    monkeypatch.setattr(reindex_vec, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing", "--task-id", "t2"])

    class MockFailClient:
        def __init__(self, *args, **kwargs):
            self.calls = 0
        def embed_batch(self, texts):
            self.calls += 1
            if self.calls == 1:
                class PingRes:
                    error = None
                    embeddings = [[0.0] * 128]
                return PingRes()
            class FailRes:
                error = "ConnectionError: connection refused"
                embeddings = None
            return FailRes()

    monkeypatch.setattr(reindex_vec, "OllamaClient", MockFailClient)
    monkeypatch.setattr(reindex_vec, "_ensure_schema", lambda p: None)

    with pytest.raises(SystemExit) as exc_info:
        reindex_vec.main()

    assert exc_info.value.code == 1

    err_cnt = find_metric_value(reader, "reindex_ollama_errors_total", {"error_class": "connection"})
    assert err_cnt == 1


def test_ollama_errors_excludes_no_translation(tmp_path, monkeypatch):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(obs, "get_meter", lambda name: provider.get_meter(name))
    monkeypatch.setattr(reindex_vec, "get_meter", lambda name: provider.get_meter(name))

    db_path = tmp_path / "content.db"
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    conn.commit()
    conn.close()

    monkeypatch.setattr(reindex_vec, "DB_PATH", db_path)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing", "--task-id", "t3"])

    monkeypatch.setattr(reindex_vec.kb_db, "list_missing_article_keys", lambda p: [("phantom-uuid", "en")])
    monkeypatch.setattr(reindex_vec.kb_db, "list_missing_question_keys", lambda p: [])
    monkeypatch.setattr(reindex_vec, "_ensure_schema", lambda p: None)

    class MockClient:
        def __init__(self, *a, **kw): pass
        def embed_batch(self, texts):
            class R:
                error = None
                embeddings = [[0.0] * 128]
            return R()

    monkeypatch.setattr(reindex_vec, "OllamaClient", MockClient)

    with pytest.raises(SystemExit):
        reindex_vec.main()

    dps = find_metric_datapoints(reader, "reindex_ollama_errors_total")
    for dp in dps:
        assert dp["labels"].get("error_class") != "no_translation"
