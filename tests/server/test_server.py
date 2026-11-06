import pytest
from fastapi.testclient import TestClient
from server.app import app
from server.db import init_server_db, get_server_db

@pytest.fixture(autouse=True)
def setup_db():
    init_server_db()
    with get_server_db() as conn:
        conn.execute("DELETE FROM feedback")
        conn.commit()

def test_receive_feedback():
    client = TestClient(app)
    payload = [{
        "client_uuid": "test-uuid-1",
        "question_uuid": "abc12345",
        "question_text": "What is Linux?",
        "selected_answer": "OS",
        "correct_answer": "OS",
        "language": "en",
        "domain_slug": "linux",
        "article_slug": "linux/intro",
        "reason_code": "typo",
        "comment": "Test comment",
        "app_version": "1.0.0",
        "locale": "en-US",
        "client_created_at": 1700000000
    }]
    response = client.post("/api/feedback", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["inserted"] == 1

    # Test duplicate insertion (should be skipped due to UNIQUE constraint on client_uuid)
    response2 = client.post("/api/feedback", json=payload)
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["status"] == "ok"
    assert data2["inserted"] == 0

def test_receive_feedback_without_question_uuid():
    client = TestClient(app)
    payload = [{
        "client_uuid": "test-uuid-no-question",
        "question_text": "",
        "selected_answer": "",
        "correct_answer": "",
        "language": "en",
        "domain_slug": "linux",
        "article_slug": "linux/intro",
        "reason_code": "article_feedback",
        "comment": "Great article!",
        "app_version": "1.0.0",
        "locale": "en-US",
        "client_created_at": 1700000000
    }]
    response = client.post("/api/feedback", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["inserted"] == 1

    with get_server_db() as conn:
        row = conn.execute("SELECT question_uuid FROM feedback WHERE client_uuid = ?", ("test-uuid-no-question",)).fetchone()
        assert row is not None
        assert row["question_uuid"] is None

