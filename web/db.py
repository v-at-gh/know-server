"""App-level configuration database (domains, campaigns)."""
import sqlite3
from pathlib import Path
from quizkb.config import REPO_ROOT

APP_DB_PATH = REPO_ROOT / "state" / "app.db"

def get_app_db():
    APP_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(APP_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_app_db():
    """Initializes the app database schema (`app.db`).
    
    Establishes the authoritative SQLite schema for the Content Management System (CMS):
      - `domains`: Stores domain metadata (slug, name, description, kind).
      - `campaigns`: Stores campaign metadata (slug, name, description, order, prerequisites).
      - `campaign_domains`: Relational mapping table linking campaigns to domains with positional order.
    """
    with get_app_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS domains (
                slug TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                kind TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                slug TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                "order" INTEGER NOT NULL,
                prerequisite_domain_slugs TEXT NOT NULL DEFAULT '[]'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS campaign_domains (
                campaign_slug TEXT NOT NULL,
                domain_slug TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (campaign_slug, domain_slug)
            )
        """)
        conn.commit()