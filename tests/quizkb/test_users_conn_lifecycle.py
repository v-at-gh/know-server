import sqlite3
from quizkb import users as kb_users

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

def test_get_user_settings_closes_connections(monkeypatch, tmp_path):
    test_db = tmp_path / "users.db"
    monkeypatch.setattr("quizkb.users.USERS_DB_PATH", test_db)
    
    kb_users.ensure_schema()
    with sqlite3.connect(test_db) as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, active_domains, active_langs) VALUES (?, ?, ?, ?)",
            ("testuser", "hash", "[]", "[]")
        )
        user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    open_count = {"n": 0}
    close_count = {"n": 0}
    real_connect = sqlite3.connect

    def tracking_connect(*a, **kw):
        conn = real_connect(*a, **kw)
        return TrackingConnection(conn, open_count, close_count)

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)

    for _ in range(10):
        kb_users.get_user_settings(user_id)

    leaked = open_count["n"] - close_count["n"]
    assert leaked <= 1, f"leaked {leaked} of {open_count['n']} opened connections in users.py"
