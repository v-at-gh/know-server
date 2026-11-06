import pytest
import json
from pathlib import Path
from fastapi.testclient import TestClient
from web.app import app
from web import auth
from web.db import init_app_db, get_app_db
import web.app as app_module

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
def client(mock_output_root, tmp_path, monkeypatch):
    from quizkb import users as kb_users
    monkeypatch.setattr(kb_users, "USERS_DB_PATH", tmp_path / "users.db")
    kb_users.ensure_schema()

    c = TestClient(app)
    c.headers.update({"Origin": "http://testserver"})
    auth.init_db()
    init_app_db()
    try:
        auth.create_user("testadmin", "password123", ["testdom"], ["en", "ru"])
    except Exception:
        pass
    with auth.get_db() as conn:
        conn.execute("UPDATE users SET generation_params = '{}' WHERE username = 'testadmin'")
        conn.commit()
    c.post("/login", data={"username": "testadmin", "password": "password123"})
    
    with get_app_db() as conn:
        conn.execute("INSERT OR REPLACE INTO domains (slug, name, description, kind) VALUES (?, ?, ?, ?)",
                     ("testdom", "Test Domain", "Desc", "pack"))
        conn.commit()
    return c


def test_generation_params_default(client):
    res = client.get("/api/v1/users/me/generation-params")
    assert res.status_code == 200
    data = res.json()
    assert data["articles_per_domain"] == 10
    assert data["questions_types"] == {"select": 5, "construct": 0}

def test_generation_params_update(client):
    res = client.put("/api/v1/users/me/generation-params",
                     json={"articles_per_domain": 6,
                           "questions_types": {"construct": 2}})
    assert res.status_code == 200
    data = res.json()
    assert data["articles_per_domain"] == 6
    assert data["questions_types"] == {"select": 5, "construct": 2}

def test_generation_params_validation(client):
    res = client.put("/api/v1/users/me/generation-params",
                     json={"questions_types": {"select": 0,
                                               "construct": 0}})
    assert res.status_code == 400
    res = client.put("/api/v1/users/me/generation-params",
                     json={"articles_per_domain": 0})
    assert res.status_code == 400
    res = client.put("/api/v1/users/me/generation-params",
                     json={"articles_per_domain": 100})
    assert res.status_code == 400

def test_domains_reflect_user_params(client, mock_output_root):
    before = client.get("/api/v1/domains?langs=en").json()
    testdom_before = next(
        d for d in before["domains"] if d["slug"] == "testdom")
    res = client.put(
        "/api/v1/users/me/generation-params",
        json={"questions_types": {"select": 10, "construct": 5}},
    )
    assert res.status_code == 200
    after = client.get("/api/v1/domains?langs=en").json()
    testdom_after = next(
        d for d in after["domains"] if d["slug"] == "testdom")
    assert (testdom_after["status"]["en"]["expected_questions"]
            > testdom_before["status"]["en"]["expected_questions"])

def test_translate_plan_uses_user_params(client, mock_output_root):
    client.put(
        "/api/v1/users/me/generation-params",
        json={"questions_types": {"select": 10, "construct": 5}},
    )
    res = client.post("/api/v1/domains/translate/plan",
                      json={"langs": ["ru"]})
    assert res.status_code == 200
    queued = [i for i in res.json()["items"] if i["action"] == "queue"]
    assert any(i["slug"] == "testdom" for i in queued)

def test_orchestrator_task_carries_generation_params(mock_output_root):
    from orchestrator.app import app as orch_app
    from orchestrator.tasks import TaskManager
    orch_app.state.task_manager = TaskManager()
    tm = orch_app.state.task_manager
    tid = tm.enqueue(
        action="bootstrap",
        domain="testdom",
        lang="en",
        generation_params={"articles_per_domain": 5, "questions_types": {"select": 3, "construct": 1}}
    )
    with tm._get_conn() as conn:
        row = conn.execute("SELECT generation_params FROM tasks WHERE id = ?", (tid,)).fetchone()
        assert row is not None
        gp = json.loads(row["generation_params"])
        assert gp["articles_per_domain"] == 5
        assert gp["questions_types"] == {"select": 3, "construct": 1}
