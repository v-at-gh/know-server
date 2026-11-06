"""Plan endpoints reflect per-domain questions_types overrides."""

import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated users.db, app.db, output tree with two domains."""
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
            {"order": 1, "slug": "art1", "title": "Article", "summary": "S", "uuid": f"u-{domain}-1"},
        ]), encoding="utf-8")
        md = out / "knowledge" / "en" / domain / "art1.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text("# Article\n" + "x" * 2500, encoding="utf-8")

        ds = out / "datasets" / "en" / f"{domain}.json"
        ds.parent.mkdir(parents=True, exist_ok=True)
        ds.write_text(json.dumps([]), encoding="utf-8")

    (out / "datasets" / "ru").mkdir(parents=True, exist_ok=True)
    (out / "datasets" / "en" / "domains.json").write_text(
        json.dumps([{"slug": "linux", "name": "Linux", "kind": "tool"},
                    {"slug": "docker", "name": "Docker", "kind": "tool"}]),
        encoding="utf-8",
    )

    from web import auth
    from web.db import init_app_db, get_app_db
    init_app_db()
    try:
        auth.create_user("testadmin", "pw", ["linux", "docker"], ["en", "ru"])
    except Exception:
        pass

    user = auth.authenticate("testadmin", "pw")
    kb_users.update_user_settings(user.id, {
        "generation_params": {
            "per_domain": {"linux": {"questions_types": {"select": 10, "construct": 0}}},
        },
    })

    with get_app_db() as conn:
        for slug, name in [("linux", "Linux"), ("docker", "Docker")]:
            conn.execute(
                "INSERT OR REPLACE INTO domains (slug, name, description, kind) VALUES (?, ?, '', 'tool')",
                (slug, name),
            )
        conn.commit()

    from web.app import app
    import web.app as web_app
    monkeypatch.setattr(web_app, "OUTPUT_ROOT", out)

    client = TestClient(app)
    client.headers.update({"Origin": "http://testserver"})
    client.post("/login", data={"username": "testadmin", "password": "pw"})
    return client, user


def test_domain_status_per_domain_questions(env):
    """api_get_domains returns per-domain expected_questions.
    Does NOT exercise _compute_translate_plan; see test_translate_plan_state_differs_via_per_domain.
    """
    client, _ = env
    r = client.post("/api/v1/domains/translate/plan", json={"langs": ["ru"]})
    assert r.status_code == 200

    r = client.get("/api/v1/domains?langs=ru")
    domains = r.json()["domains"]
    linux = next(d for d in domains if d["slug"] == "linux")
    docker = next(d for d in domains if d["slug"] == "docker")
    assert linux["status"]["ru"]["expected_questions"] == 10
    assert docker["status"]["ru"]["expected_questions"] == 5


def test_bootstrap_plan_per_domain_questions(env):
    client, _ = env
    r = client.get("/api/v1/domains?langs=en")
    domains = r.json()["domains"]
    linux = next(d for d in domains if d["slug"] == "linux")
    docker = next(d for d in domains if d["slug"] == "docker")
    assert linux["status"]["en"]["expected_questions"] == 10
    assert docker["status"]["en"]["expected_questions"] == 5


def test_compute_translate_plan_passes_per_domain_qt_to_evaluate(monkeypatch, env):
    """_compute_translate_plan resolves per-domain questions_types and passes it
    to evaluate_domain_status.

    Boundary test: asserts the exact dict passed to the state evaluator.
    For 'linux' with per_domain.questions_types.select=10, evaluator must
    receive {"select": 10, "construct": 0}. For 'docker' (no per_domain override),
    evaluator must receive global {"select": 5, "construct": 0}.

    Catches sabotage: if _compute_translate_plan passes questions_types=None
    instead of per-domain qt, this test fails with None assertion, independent
    of state.py threshold logic.
    """
    import web.app as web_app
    from web.app import _compute_translate_plan

    client, user = env

    # Spy: capture every evaluate_domain_status call
    calls: list[dict] = []
    original = web_app.evaluate_domain_status

    def _spy(slug, lang, questions_types=None, cache=None):
        calls.append({
            "slug": slug,
            "lang": lang,
            "questions_types": questions_types,
        })
        return original(slug, lang, questions_types=questions_types, cache=cache)

    monkeypatch.setattr(web_app, "evaluate_domain_status", _spy)

    _compute_translate_plan(None, ["ru"], user_id=user.id)

    # Linux: per-domain override applies
    linux_call = next(
        (c for c in calls if c["slug"] == "linux" and c["lang"] == "ru"),
        None,
    )
    assert linux_call is not None, (
        f"evaluate_domain_status not called for linux/ru. All calls: {calls}"
    )
    assert linux_call["questions_types"] == {"select": 10, "construct": 0}, (
        f"Expected per-domain qt for linux (select=10), got {linux_call['questions_types']}. "
        f"If None or {{'select': 5, 'construct': 0}}, per-domain resolution is broken."
    )

    # Docker: no per_domain override → global questions_types inherited
    docker_call = next(
        (c for c in calls if c["slug"] == "docker" and c["lang"] == "ru"),
        None,
    )
    assert docker_call is not None
    assert docker_call["questions_types"] == {"select": 5, "construct": 0}, (
        f"Docker has no per_domain override; expected global "
        f"{{'select': 5, 'construct': 0}}, got {docker_call['questions_types']}"
    )
