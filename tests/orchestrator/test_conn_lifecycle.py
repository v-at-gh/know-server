import sqlite3
from orchestrator.tasks import TaskManager

class TrackingConnection:
    def __init__(self, conn, open_dict, close_dict):
        self._conn = conn
        open_dict["n"] += 1
        self._close_dict = close_dict

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, val):
        self._conn.row_factory = val

    def close(self):
        self._close_dict["n"] += 1
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

def test_task_manager_closes_connections(monkeypatch, tmp_path):
    test_db = tmp_path / "tasks.db"
    monkeypatch.setattr("orchestrator.tasks.DB_PATH", test_db)
    
    open_count = {"n": 0}
    close_count = {"n": 0}
    real_connect = sqlite3.connect

    def tracking_connect(*a, **kw):
        conn = real_connect(*a, **kw)
        return TrackingConnection(conn, open_count, close_count)

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)
    tm = TaskManager()
    for _ in range(5):
        tm.enqueue(action="generate", domain="x", lang="en")
        tm.list_tasks()
        tm.get_active_count()
        tm.reconcile_orphaned_tasks()

    leaked = open_count["n"] - close_count["n"]
    assert leaked <= 2, f"leaked {leaked} of {open_count['n']} opened connections"
