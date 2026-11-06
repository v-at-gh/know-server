import json
import pytest
from pathlib import Path
import sqlite3
from scripts import reindex_vec

def test_reindex_writes_progress_and_marker(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "content.db"
    # Create empty sqlite db
    conn = sqlite3.connect(db_path)
    conn.commit()
    conn.close()

    monkeypatch.setattr(reindex_vec, "DB_PATH", db_path)
    monkeypatch.setattr(reindex_vec, "REPO_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["reindex_vec.py", "--missing", "--task-id", "test_task_xyz"])

    # Mock OllamaClient so health check passes without real service
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

    progress_file = tmp_path / "state" / "tasks" / "test_task_xyz" / "progress.json"
    assert progress_file.exists()
    data = json.loads(progress_file.read_text(encoding="utf-8"))
    assert data["phase"] == "reindex"
    assert "done" in data
    assert "total" in data

    captured = capsys.readouterr()
    assert "=== Reindex Completed Successfully ===" in captured.out or "=== Reindex Completed Successfully ===" in captured.err
