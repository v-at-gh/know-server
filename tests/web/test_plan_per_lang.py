"""per_lang.translate_domains filter in translate plan."""

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
    for domain in ("linux", "docker", "python"):
        syl = out / "syllabus" / "en" / f"{domain}.json"
        syl.parent.mkdir(parents=True, exist_ok=True)
        syl.write_text(json.dumps([
            {"order": 1, "slug": "art1", "title": "Art", "summary": "S", "uuid": f"u-{domain}-1"},
        ]), encoding="utf-8")
        md = out / "knowledge" / "en" / domain / "art1.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text("# A\n" + "x" * 2500, encoding="utf-8")

    (out / "datasets" / "en").mkdir(parents=True, exist_ok=True)
    (out / "datasets" / "ru").mkdir(parents=True, exist_ok=True)
    (out / "datasets" / "en" / "domains.json").write_text(
        json.dumps([{"slug": s, "name": s.title(), "kind": "tool"}
                    for s in ("linux", "docker", "python")]),
        encoding="utf-8",
    )

    from web import auth
    from web.db import init_app_db, get_app_db
    init_app_db()
    try:
        auth.create_user("testadmin", "pw", ["linux", "docker", "python"], ["en", "ru", "uk", "es"])
    except Exception:
        pass

    user = auth.authenticate("testadmin", "pw")
    kb_users.update_user_settings(user.id, {
        "generation_params": {
            "per_lang": {"uk": {"translate_domains": ["linux", "docker"]}},
        },
    })

    with get_app_db() as conn:
        for slug in ("linux", "docker", "python"):
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


def test_per_lang_filters_disallowed_domains(env):
    r = env.post("/api/v1/domains/translate/plan", json={"langs": ["uk"]})
    items = r.json()["items"]
    python_uk = next(i for i in items if i["slug"] == "python" and i["lang"] == "uk")
    assert python_uk["action"] == "skip"
    assert python_uk["reason"] == "filtered_by_per_lang"
    linux_uk = next(i for i in items if i["slug"] == "linux" and i["lang"] == "uk")
    assert linux_uk["reason"] != "filtered_by_per_lang"


def test_per_lang_unset_defaults_to_all(env):
    r = env.post("/api/v1/domains/translate/plan", json={"langs": ["ru"]})
    items = r.json()["items"]
    python_ru = next(i for i in items if i["slug"] == "python" and i["lang"] == "ru")
    assert python_ru["reason"] != "filtered_by_per_lang"


def test_per_lang_empty_list_filters_all(env):
    from quizkb import users as kb_users
    from web import auth
    user = auth.authenticate("testadmin", "pw")
    kb_users.update_user_settings(user.id, {
        "generation_params": {
            "per_lang": {"uk": {"translate_domains": ["linux", "docker"]},
                         "es": {"translate_domains": []}},
        },
    })
    r = env.post("/api/v1/domains/translate/plan", json={"langs": ["es"]})
    items = r.json()["items"]
    for lang in ("es",):
        for item in [i for i in items if i["lang"] == lang]:
            assert item["action"] == "skip"
            assert item["reason"] == "filtered_by_per_lang"
