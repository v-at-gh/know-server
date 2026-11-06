import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from web.app import app
from web import auth
from web.db import init_app_db
import web.app as app_module
from quizkb.state import domain_questions


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

    # Syllabus for testdom
    syl_data = [
        {"order": 1, "slug": "art1", "title": "Article One", "summary": "Summary 1"},
        {"order": 2, "slug": "art2", "title": "Article Two", "summary": "Summary 2"},
    ]
    (syl_dir / "testdom.json").write_text(json.dumps(syl_data), encoding="utf-8")

    # Knowledge markdown files
    (know_dir / "art1.md").write_text("# Article One\n" + "x" * 2500, encoding="utf-8")
    (know_dir / "art2.md").write_text("# Short\nShort text", encoding="utf-8")

    # Datasets
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
        {
            "uuid": "u2222222-2222-2222-2222-222222222222",
            "text": "Question 2 text?",
            "answer": "Answer 2",
            "distractors": [{"text": "D2"}],
            "hints": [],
            "difficulty": 2,
            "relatedArticleSlug": "testdom/art2",
        },
    ]
    (ds_en_dir / "testdom.json").write_text(json.dumps(en_q_data), encoding="utf-8")

    monkeypatch.setattr(app_module, "OUTPUT_ROOT", out_dir)
    return out_dir


@pytest.fixture
def client():
    c = TestClient(app)
    c.headers.update({"Origin": "http://testserver"})
    auth.init_db()
    init_app_db()
    try:
        auth.create_user("testadmin", "password123", ["testdom"], ["en", "ru"])
    except Exception:
        pass
    c.post("/login", data={"username": "testadmin", "password": "password123"})
    return c


def test_domain_questions_helper(tmp_path: Path):
    en_dir = tmp_path / "datasets" / "en"
    ru_dir = tmp_path / "datasets" / "ru"
    en_dir.mkdir(parents=True, exist_ok=True)
    ru_dir.mkdir(parents=True, exist_ok=True)

    en_data = [
        {
            "uuid": "q1",
            "text": "What is Linux?",
            "answer": "Operating System",
            "distractors": [{"text": "Browser", "why": "Incorrect"}],
            "hints": ["Free OS"],
            "difficulty": 1,
            "relatedArticleSlug": "linux/overview",
        },
        {
            "uuid": "q2",
            "text": "What is bash?",
            "answer": "Shell",
            "distractors": [{"text": "Kernel"}],
            "hints": [],
            "difficulty": 2,
            "relatedArticleSlug": "linux/overview",
            "archived": True,
        },
        {
            "uuid": "q3",
            "text": "What is sudo?",
            "answer": "Superuser do",
            "distractors": [{"text": "Nothing"}],
            "hints": [],
            "difficulty": 1,
            "relatedArticleSlug": "linux/permissions",
        },
    ]

    ru_data = [
        {
            "uuid": "q1",
            "text": "Что такое Linux?",
            "answer": "Операционная система",
            "distractors": [{"text": "Браузер", "why": "Неверно"}],
            "hints": ["Бесплатная ОС"],
            "difficulty": 1,
            "relatedArticleSlug": "linux/overview",
        },
    ]

    (en_dir / "linux.json").write_text(json.dumps(en_data), encoding="utf-8")
    (ru_dir / "linux.json").write_text(json.dumps(ru_data), encoding="utf-8")

    entries = domain_questions(tmp_path, "ru", "linux")
    assert len(entries) == 3

    q1 = next(e for e in entries if e.uuid == "q1")
    assert q1.state == "ready"
    assert q1.archived is False
    assert q1.related_article_slug == "overview"

    q2 = next(e for e in entries if e.uuid == "q2")
    assert q2.state == "archived"
    assert q2.archived is True

    q3 = next(e for e in entries if e.uuid == "q3")
    assert q3.state == "missing"
    assert q3.archived is False


def test_api_get_domain_articles(client, mock_output_root):
    res = client.get("/api/v1/domains/testdom/articles?lang=en")
    assert res.status_code == 200
    data = res.json()
    assert data["domain"] == "testdom"
    assert data["lang"] == "en"
    assert data["total"] == 2
    
    art1 = next(a for a in data["articles"] if a["slug"] == "art1")
    assert art1["state"] == "ready"
    assert art1["bytes"] > 2000

    art2 = next(a for a in data["articles"] if a["slug"] == "art2")
    assert art2["state"] == "short"

    # Status filter test
    res_filtered = client.get("/api/v1/domains/testdom/articles?lang=en&status=ready")
    assert res_filtered.status_code == 200
    filtered_data = res_filtered.json()
    assert filtered_data["total"] == 1
    assert filtered_data["articles"][0]["slug"] == "art1"


def test_api_get_domain_questions(client, mock_output_root):
    res = client.get("/api/v1/domains/testdom/questions?lang=en")
    assert res.status_code == 200
    data = res.json()
    assert data["domain"] == "testdom"
    assert data["total"] == 2

    # Article slug filter
    res_art1 = client.get("/api/v1/domains/testdom/questions?lang=en&article_slug=art1")
    assert res_art1.status_code == 200
    data_art1 = res_art1.json()
    assert data_art1["total"] == 1
    assert data_art1["questions"][0]["related_article_slug"] == "art1"


def test_api_get_article_detail(client, mock_output_root):
    res = client.get("/api/v1/articles/en/testdom/art1")
    assert res.status_code == 200
    data = res.json()
    assert data["slug"] == "art1"
    assert data["title"] == "Article One"
    assert len(data["questions"]) == 1
    assert data["questions"][0]["uuid"] == "u1111111-1111-1111-1111-111111111111"

    # 404 test
    res_404 = client.get("/api/v1/articles/en/testdom/nonexistent")
    assert res_404.status_code == 404


def test_api_get_question_detail(client, mock_output_root):
    res = client.get("/api/v1/questions/u1111111-1111-1111-1111-111111111111")
    assert res.status_code == 200
    data = res.json()
    assert data["uuid"] == "u1111111-1111-1111-1111-111111111111"
    assert data["domain_slug"] == "testdom"
    assert len(data["languages"]) > 0

    # 404 test
    res_404 = client.get("/api/v1/questions/nonexistent-uuid")
    assert res_404.status_code == 404
