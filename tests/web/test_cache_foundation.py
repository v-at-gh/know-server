import pytest
import json
from pathlib import Path
from fastapi.testclient import TestClient
from web.app import app
from web import auth
from web.db import init_app_db, get_app_db
import web.app as app_module
from quizkb.io_utils import cache_stats, reset_cache, load_json, save_json

@pytest.fixture
def mock_output_root(tmp_path: Path, monkeypatch):
    out_dir = tmp_path / "output"
    syl_dir = out_dir / "syllabus" / "en"
    know_dir = out_dir / "knowledge" / "en" / "testdom"
    ds_en_dir = out_dir / "datasets" / "en"
    ds_ru_dir = out_dir / "datasets" / "ru"

    syl_dir.mkdir(parents=True, exist_ok=True)
    know_dir.mkdir(parents=True, exist_ok=True)
    ds_en_dir.mkdir(parents=True, exist_ok=True)
    ds_ru_dir.mkdir(parents=True, exist_ok=True)

    syl_data = [
        {"order": 1, "slug": "art1", "title": "Article One", "summary": "Summary 1"},
    ]
    (syl_dir / "testdom.json").write_text(json.dumps(syl_data), encoding="utf-8")
    (know_dir / "art1.md").write_text("# Article One\n" + "x" * 2500, encoding="utf-8")

    en_q_data = [
        {
            "uuid": "u1111111-1111-1111-1111-111111111111",
            "text": "Question 1 text?",
            "answer": "Answer 1",
            "distractors": [{"text": "D1"}],
            "hints": ["H1"],
            "difficulty": 1,
            "relatedArticleSlug": "testdom/art1",
        },
    ]
    (ds_en_dir / "testdom.json").write_text(json.dumps(en_q_data), encoding="utf-8")

    monkeypatch.setattr(app_module, "OUTPUT_ROOT", out_dir)
    return out_dir


@pytest.fixture
def client(mock_output_root):
    c = TestClient(app)
    c.headers.update({"Origin": "http://testserver"})
    auth.init_db()
    init_app_db()
    try:
        auth.create_user("testadmin", "password123", ["testdom"], ["en", "ru"])
    except Exception:
        pass
    c.post("/login", data={"username": "testadmin", "password": "password123"})
    
    with get_app_db() as conn:
        conn.execute("INSERT OR REPLACE INTO domains (slug, name, description, kind) VALUES (?, ?, ?, ?)",
                     ("testdom", "Test Domain", "Desc", "pack"))
        conn.commit()
    return c


def test_load_json_cache_hit(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"a": 1}')
    reset_cache()
    v1 = load_json(p)
    v2 = load_json(p)
    s = cache_stats()
    assert s["miss"] == 1
    assert s["hit"] == 1
    assert v1 == v2

def test_load_json_cache_invalidates_on_mtime(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"a": 1}')
    reset_cache()
    load_json(p)
    import os, time
    time.sleep(0.01)
    p.write_text('{"a": 2}')
    v = load_json(p)
    assert v == {"a": 2}
    assert cache_stats()["miss"] == 2

def test_save_json_invalidates(tmp_path):
    p = tmp_path / "x.json"
    save_json(p, {"a": 1})
    load_json(p)
    save_json(p, {"a": 2})
    assert load_json(p) == {"a": 2}

def test_actual_content_matches_domain_summary(tmp_path):
    from quizkb.state import actual_content, domain_summary
    (tmp_path / "syllabus" / "en").mkdir(parents=True)
    (tmp_path / "syllabus" / "en" / "testdom.json").write_text('[{"slug": "art1", "title": "A1"}]')
    (tmp_path / "datasets" / "en").mkdir(parents=True)
    (tmp_path / "datasets" / "en" / "testdom.json").write_text('[{"uuid": "q1", "relatedArticleSlug": "testdom/art1"}]')
    (tmp_path / "knowledge" / "en" / "testdom").mkdir(parents=True)
    (tmp_path / "knowledge" / "en" / "testdom" / "art1.md").write_text("x" * 2500)

    a = actual_content(tmp_path, "testdom", "en")
    s = domain_summary(tmp_path, "en", "testdom")
    assert a.articles == s.articles
    assert a.questions == s.questions
    assert a.short_articles == s.short_articles

def test_evaluate_domain_status_backward_compat(client, mock_output_root):
    res = client.get("/domain/en/testdom")
    assert res.status_code == 200
    assert "Article One" in res.text

def test_evaluate_domain_status_user_questions_types(tmp_path):
    from quizkb.state import evaluate_domain_status as eval_v2
    (tmp_path / "syllabus" / "en").mkdir(parents=True)
    (tmp_path / "syllabus" / "en" / "testdom.json").write_text('[{"slug": "art1", "title": "A1"}]')
    (tmp_path / "datasets" / "en").mkdir(parents=True)
    (tmp_path / "datasets" / "en" / "testdom.json").write_text('[{"uuid": "q1", "relatedArticleSlug": "testdom/art1"}]')
    (tmp_path / "knowledge" / "en" / "testdom").mkdir(parents=True)
    (tmp_path / "knowledge" / "en" / "testdom" / "art1.md").write_text("x" * 2500)

    default = eval_v2(tmp_path, "testdom", "en")
    high = eval_v2(tmp_path, "testdom", "en",
                   questions_types={"select": 8, "construct": 4})
    assert default["expected_questions"] != high["expected_questions"]

def test_only_active_filter(client, mock_output_root):
    auth.init_db()
    out_dir = mock_output_root
    for dslug in ["testdom", "otherdom"]:
        syl_dir = out_dir / "syllabus" / "en"
        ds_dir = out_dir / "datasets" / "en"
        know_dir = out_dir / "knowledge" / "en" / dslug
        syl_dir.mkdir(parents=True, exist_ok=True)
        ds_dir.mkdir(parents=True, exist_ok=True)
        know_dir.mkdir(parents=True, exist_ok=True)
        (syl_dir / f"{dslug}.json").write_text(json.dumps([{"slug": "art1", "title": "A1"}]), encoding="utf-8")
        (ds_dir / f"{dslug}.json").write_text(json.dumps([{"uuid": f"uuid-{dslug}", "text": "Q", "answer": "A", "relatedArticleSlug": f"{dslug}/art1"}]), encoding="utf-8")
        (know_dir / "art1.md").write_text("x" * 2500, encoding="utf-8")

    with get_app_db() as conn:
        conn.execute("INSERT OR IGNORE INTO domains (slug, name, description, kind) VALUES ('otherdom', 'Other', 'Other Desc', 'tool')")
        conn.commit()

    with auth.get_db() as conn:
        conn.execute("UPDATE users SET active_domains = ? WHERE username = ?", (json.dumps(["testdom"]), "testadmin"))
        conn.commit()

    res_all = client.get("/api/v1/domains?langs=en&only_active=false")
    assert res_all.status_code == 200
    slugs_all = {d["slug"] for d in res_all.json()["domains"]}
    assert {"testdom", "otherdom"} <= slugs_all

    res_active = client.get("/api/v1/domains?langs=en&only_active=true")
    assert res_active.status_code == 200
    slugs_active = {d["slug"] for d in res_active.json()["domains"]}
    assert slugs_active == {"testdom"}

def test_domains_endpoint_uses_cache(client, mock_output_root):
    reset_cache()
    client.get("/api/v1/domains?langs=en,ru")
    before = cache_stats()
    client.get("/api/v1/domains?langs=en,ru")
    after = cache_stats()
    assert after["hit"] > before["hit"]
    assert after["miss"] == before["miss"]

def test_translate_plan_uses_cache(client, mock_output_root):
    reset_cache()
    client.post("/api/v1/domains/translate/plan",
                json={"langs": ["ru"]})
    before = cache_stats()
    client.post("/api/v1/domains/translate/plan",
                json={"langs": ["ru"]})
    after = cache_stats()
    assert after["hit"] > before["hit"]
    assert after["miss"] == before["miss"]

def test_question_edit_save_no_cache_poisoning(client, mock_output_root):
    reset_cache()
    res = client.post(
        "/question/u1111111-1111-1111-1111-111111111111/edit",
        params={"lang": "en", "domain": "testdom"},
        data={"text": "Mutated text", "answer": "", "type": "select"},
        follow_redirects=False,
    )
    assert res.status_code == 400, "invalid edit must be rejected"

    r2 = client.get("/api/v1/questions/u1111111-1111-1111-1111-111111111111")
    assert r2.status_code == 200
    data = r2.json()
    en_lang = next(l for l in data["languages"] if l["lang"] == "en")
    assert en_lang["text"] == "Question 1 text?", \
        "cache poisoning: draft leaked into subsequent read"
    assert en_lang["text"] != "Mutated text"

def test_legacy_domain_route_uses_cached_state(client, mock_output_root):
    reset_cache()
    client.get("/api/v1/domains?langs=en")
    before = cache_stats()
    client.get("/domain/en/testdom")
    after = cache_stats()
    assert after["miss"] == before["miss"], \
        "legacy route re-read from disk despite warm cache"
    assert after["hit"] > before["hit"]

def test_cache_fresh_bypass(tmp_path):
    from quizkb.io_utils import load_json_fresh, reset_cache, load_json
    p = tmp_path / "x.json"
    p.write_text('{"a": 1}')
    reset_cache()
    load_json(p)
    p.write_text('{"a": 2}')
    assert load_json_fresh(p) == {"a": 2}
