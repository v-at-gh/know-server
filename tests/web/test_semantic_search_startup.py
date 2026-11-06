import sqlite3
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import sqlite_vec
from quizkb import db as kb_db
from quizkb.config import OLLAMA_EMBED_MODEL, EMBED_DIMENSIONS


def _make_db_with_meta(tmp_path, dims=128, model=None):
    model = model or OLLAMA_EMBED_MODEL
    db = tmp_path / "content.db"
    conn = sqlite3.connect(db)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    conn.execute(
        "INSERT INTO schema_meta (key, value) VALUES ('embed_model', ?)",
        (model,)
    )
    conn.execute(
        "INSERT INTO schema_meta (key, value) VALUES ('embed_dimensions', ?)",
        (str(dims),)
    )
    conn.commit()
    conn.close()
    return db


def _make_db_without_meta(tmp_path):
    db = tmp_path / "content.db"
    conn = sqlite3.connect(db)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(kb_db.SCHEMA)
    conn.commit()
    conn.close()
    return db


@patch("web.app.OllamaClient")
def test_startup_enables_semantic_when_meta_matches(
    mock_client_cls, tmp_path, monkeypatch
):
    db = _make_db_with_meta(tmp_path)

    mock_client = MagicMock()
    mock_client.embed_batch.return_value = MagicMock(error=None)
    mock_client_cls.return_value = mock_client

    import web.app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db)

    with TestClient(app_module.app):
        assert app_module.app.state.semantic_available is True
        assert app_module.app.state.ollama_client is mock_client


@patch("web.app.OllamaClient")
def test_startup_disables_semantic_when_meta_absent(
    mock_client_cls, tmp_path, monkeypatch
):
    db = _make_db_without_meta(tmp_path)
    import web.app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db)

    with TestClient(app_module.app):
        assert app_module.app.state.semantic_available is False
        assert app_module.app.state.ollama_client is None


@patch("web.app.OllamaClient")
def test_startup_disables_semantic_when_model_mismatch(
    mock_client_cls, tmp_path, monkeypatch
):
    db = _make_db_with_meta(tmp_path, model="other-model")
    import web.app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db)

    with TestClient(app_module.app):
        assert app_module.app.state.semantic_available is False


@patch("web.app.OllamaClient")
def test_startup_disables_semantic_when_ollama_errors(
    mock_client_cls, tmp_path, monkeypatch
):
    db = _make_db_with_meta(tmp_path)

    mock_client = MagicMock()
    mock_client.embed_batch.return_value = MagicMock(error="connection refused")
    mock_client_cls.return_value = mock_client

    import web.app as app_module
    monkeypatch.setattr(app_module, "DB_PATH", db)

    with TestClient(app_module.app):
        assert app_module.app.state.semantic_available is False
