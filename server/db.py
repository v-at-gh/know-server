import sqlite3
from pathlib import Path
from quizkb.config import REPO_ROOT

DB_PATH = REPO_ROOT / "state" / "server.db"

def get_server_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_server_db():
    with get_server_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_uuid TEXT UNIQUE, -- To prevent duplicate uploads
                question_uuid TEXT,
                question_text TEXT,
                selected_answer TEXT,
                correct_answer TEXT,
                language TEXT,
                domain_slug TEXT,
                article_slug TEXT,
                reason_code TEXT,
                comment TEXT,
                app_version TEXT,
                locale TEXT,
                client_created_at INTEGER,
                server_received_at INTEGER,
                resolved_at INTEGER
            )
        """)
        try:
            conn.execute("ALTER TABLE feedback ADD COLUMN resolved_at INTEGER")
        except sqlite3.OperationalError:
            pass
        conn.commit()
