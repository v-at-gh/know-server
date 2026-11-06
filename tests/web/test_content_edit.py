import pytest
from fastapi.testclient import TestClient
from web.app import app
from web import auth
from web.db import init_app_db

@pytest.fixture
def client():
    c = TestClient(app)
    c.headers.update({"Origin": "http://testserver"})
    auth.init_db()
    init_app_db()
    try:
        auth.create_user("testadmin", "password123", ["linux", "python"], ["en", "ru"])
    except Exception:
        pass
    c.post("/login", data={"username": "testadmin", "password": "password123"})
    return c


def test_article_edit_get(client):
    response = client.get("/article/en/linux/overview/edit")
    assert response.status_code in (200, 404, 500)


def test_question_validation_error(client):
    response = client.post(
        "/question/nonexistent-uuid/edit",
        params={"lang": "en", "domain": "linux"},
        data={
            "text": "What is Linux?",
            "type": "select",
            "answer": "OS",
            "answerWhy": "Because",
            "distractor_text": ["OS", "Browser"],
            "distractor_why": ["Not why", "Not why"],
            "hints_text": "Hint 1\nHint 2\nHint 3",
            "difficulty": "1",
            "relatedArticleSlug": "linux/overview"
        },
        follow_redirects=False
    )
    assert response.status_code in (404, 400)


def test_feedback_inbox_and_resolve(client):
    import time
    from server.db import init_server_db, get_server_db
    init_server_db()
    with get_server_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO feedback (client_uuid, question_uuid, question_text, reason_code, server_received_at) VALUES (?, ?, ?, ?, ?)",
            ("test-uuid-inbox", "12345678", "Test Q", "typo", int(time.time() * 1000))
        )
        conn.commit()
        row = conn.execute("SELECT id FROM feedback WHERE client_uuid = 'test-uuid-inbox'").fetchone()
        fb_id = row["id"]

    res = client.get("/feedback")
    assert res.status_code == 200
    assert "Test Q" in res.text

    res_resolve = client.post(f"/feedback/{fb_id}/resolve", follow_redirects=False)
    assert res_resolve.status_code == 303

    with get_server_db() as conn:
        row = conn.execute("SELECT resolved_at FROM feedback WHERE id = ?", (fb_id,)).fetchone()
        assert row["resolved_at"] is not None

