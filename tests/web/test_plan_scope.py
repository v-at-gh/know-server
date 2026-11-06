"""scope=active|all query parameter in plan endpoints."""

import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    from quizkb import users as kb_users
    monkeypatch.setattr(kb_users, "USERS_DB_PATH", tmp_path / "users.db")
    if hasattr(kb_users, "_users_db_path"):
        monkeypatch.setattr(kb_users, "_users_db_path", lambda: tmp_path / "users.db")
    kb_users.ensure_schema()

    out = tmp_path / "output"
    for domain in ("linux", "docker"):
        syl = out / "syllabus" / "en" / f"{domain}.json"
        syl.parent.mkdir(parents=True, exist_ok=True)
        syl.write_text(json.dumps([
            {"order": 1, "slug": "art1", "title": "A", "summary": "S", "uuid": f"u-{domain}"},
        ]), encoding="utf-8")
        md = out / "knowledge" / "en" / domain / "art1.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text("# A\n" + "x" * 2500, encoding="utf-8")
    (out / "datasets" / "en").mkdir(parents=True, exist_ok=True)
    (out / "datasets" / "ru").mkdir(parents=True, exist_ok=True)
    (out / "datasets" / "en" / "domains.json").write_text(
        json.dumps([{"slug": "linux", "name": "L", "kind": "tool"},
                    {"slug": "docker", "name": "D", "kind": "tool"}]),
        encoding="utf-8",
    )

    from web import auth
    from web.db import init_app_db, get_app_db
    init_app_db()
    try:
        auth.create_user("testadmin", "pw", ["linux"], ["en", "ru"])
    except Exception:
        pass

    with get_app_db() as conn:
        for slug in ("linux", "docker"):
            conn.execute(
                "INSERT OR REPLACE INTO domains (slug, name, description, kind) VALUES (?, ?, '', 'tool')",
                (slug, slug.title()),
            )
        conn.commit()

    from web.app import app
    import web.app as web_app
    monkeypatch.setattr(web_app, "OUTPUT_ROOT", out)
    client = TestClient(app)
    client.headers.update({"Origin": "http://testserver"})
    client.post("/login", data={"username": "testadmin", "password": "pw"})
    return client


def test_default_scope_all_includes_inactive(env):
    r = env.post("/api/v1/domains/translate/plan", json={"langs": ["ru"]})
    items = r.json()["items"]
    docker_items = [i for i in items if i["slug"] == "docker"]
    assert len(docker_items) > 0


def test_scope_active_filters_inactive(env):
    r = env.post("/api/v1/domains/translate/plan",
                 json={"langs": ["ru"], "scope": "active"})
    items = r.json()["items"]
    docker_items = [i for i in items if i["slug"] == "docker"]
    assert len(docker_items) == 0
    linux_items = [i for i in items if i["slug"] == "linux"]
    assert len(linux_items) > 0


def test_scope_invalid_returns_400(env):
    r = env.post("/api/v1/domains/translate/plan",
                 json={"langs": ["ru"], "scope": "bogus"})
    assert r.status_code in (400, 422)
