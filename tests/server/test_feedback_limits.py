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

def make_feedback_item(idx: int, question_text: str = "Sample question text"):
    return {
        "client_uuid": f"uuid-{idx}",
        "question_uuid": "q-123",
        "question_text": question_text,
        "selected_answer": "A",
        "correct_answer": "B",
        "language": "en",
        "domain_slug": "linux",
        "article_slug": "linux/intro",
        "reason_code": "incorrect",
        "comment": "Sample comment",
        "app_version": "1.0.0",
        "locale": "en-US",
        "client_created_at": 1700000000 + idx,
    }

def test_batch_exceeds_max_returns_413():
    client = TestClient(app)
    batch = [make_feedback_item(i) for i in range(101)]
    response = client.post("/api/feedback", json=batch)
    assert response.status_code == 413
    assert "batch exceeds 100 items" in response.text

def test_batch_exact_max_returns_200():
    client = TestClient(app)
    batch = [make_feedback_item(i) for i in range(100)]
    response = client.post("/api/feedback", json=batch)
    assert response.status_code == 200
    assert response.json()["inserted"] == 100

def test_field_exceeding_max_length_returns_422():
    client = TestClient(app)
    oversized_text = "x" * 9000
    batch = [make_feedback_item(1, question_text=oversized_text)]
    response = client.post("/api/feedback", json=batch)
    assert response.status_code == 422

def test_happy_path_small_batch_returns_200():
    client = TestClient(app)
    batch = [make_feedback_item(i) for i in range(3)]
    response = client.post("/api/feedback", json=batch)
    assert response.status_code == 200
    assert response.json()["inserted"] == 3
