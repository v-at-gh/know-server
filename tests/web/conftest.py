import pytest
from pathlib import Path
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from tests.fixtures_pack_web import make_minimal_output_tree, make_subprocess_result

# NOTE: TestClient does NOT send Origin header by default. Tests doing
# state-changing requests with session cookie must either:
# - use admin_client / client_logged_in fixture (Origin preset), or
# - add headers={"Origin": "http://testserver"} to the request


@pytest.fixture(autouse=True)
def _isolated_app_db(tmp_path, monkeypatch):
    import web.db
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(web.db, "APP_DB_PATH", db_path)
    web.db.init_app_db()


@pytest.fixture(autouse=True)
def _clear_readiness_cache():
    import web.app as web_app
    web_app._PACK_READINESS_CACHE.clear()
    yield
    web_app._PACK_READINESS_CACHE.clear()


@pytest.fixture
def isolated_users_db(tmp_path, monkeypatch):
    from quizkb import users as kb_users
    db_path = tmp_path / "users.db"
    monkeypatch.setattr(kb_users, "USERS_DB_PATH", db_path)
    if hasattr(kb_users, "_users_db_path"):
        monkeypatch.setattr(kb_users, "_users_db_path", lambda: db_path)
    kb_users.ensure_schema()
    return db_path


@pytest.fixture
def isolated_packs_json(tmp_path, monkeypatch):
    import quizkb.config as config
    packs_path = tmp_path / "packs.json"
    monkeypatch.setattr(config, "PACKS_JSON", packs_path)
    monkeypatch.setattr("quizkb.packs.PACKS_JSON", packs_path, raising=False)
    return packs_path


@pytest.fixture
def isolated_campaigns(monkeypatch):
    """Isolate CAMPAIGNS from state/config and state/app.db fallback.

    quizkb.config.CAMPAIGNS and quizkb.state.CAMPAIGNS are module-level
    bindings resolved at import. Both must be patched to affect
    campaign_detail() / campaign_view() at runtime.
    """
    test_campaigns = [
        {
            "slug": "testcamp",
            "name": "Test Campaign",
            "description": "Fixture campaign for tests",
            "order": 1,
            "domainSlugs": ["alpha"],
            "prerequisiteDomainSlugs": [],
            "prerequisiteCampaignSlugs": [],
        },
    ]
    monkeypatch.setattr("quizkb.config.CAMPAIGNS", test_campaigns)
    monkeypatch.setattr("quizkb.state.CAMPAIGNS", test_campaigns)
    return test_campaigns


@pytest.fixture
def isolated_output_tree(tmp_path, monkeypatch):
    import web.app as web_app
    out = make_minimal_output_tree(tmp_path)
    monkeypatch.setattr(web_app, "OUTPUT_ROOT", out)
    return out


@pytest.fixture
def isolated_qaps(tmp_path, monkeypatch):
    """Isolate QAPS_DIR and QAPS_INDEX from real state/.

    Prevents tests from writing .qap artifacts or manifest.json into
    state/qaps/ or state/ during pytest runs.
    """
    import quizkb.config as config
    import web.app as web_app
    qaps_dir = tmp_path / "qaps"
    qaps_index = tmp_path / "manifest.json"
    monkeypatch.setattr(config, "QAPS_DIR", qaps_dir)
    monkeypatch.setattr(config, "QAPS_INDEX", qaps_index)
    monkeypatch.setattr(web_app, "QAPS_DIR", qaps_dir, raising=False)
    monkeypatch.setattr(web_app, "QAPS_INDEX", qaps_index, raising=False)
    monkeypatch.setattr("quizkb.packs.QAPS_DIR", qaps_dir, raising=False)
    monkeypatch.setattr("quizkb.packs.QAPS_INDEX", qaps_index, raising=False)
    return qaps_dir, qaps_index


@pytest.fixture
def subprocess_spy(monkeypatch):
    import web.app as web_app
    spy_data = {
        "calls": [],
        "returncode": 0,
        "stdout": "ok",
        "stderr": "",
        "raises": None,
    }

    def fake_run(cmd, **kwargs):
        spy_data["calls"].append((cmd, kwargs))
        if spy_data["raises"]:
            raise spy_data["raises"]
        return make_subprocess_result(
            returncode=spy_data["returncode"],
            stdout=spy_data["stdout"],
            stderr=spy_data["stderr"],
        )

    monkeypatch.setattr(web_app.subprocess, "run", fake_run)
    return spy_data


@pytest.fixture
def admin_client(
    isolated_users_db,
    isolated_packs_json,
    isolated_output_tree,
    isolated_qaps,
):
    from web.app import app
    from web import auth
    from web.db import init_app_db
    init_app_db()
    try:
        auth.create_user("admin", "pw", ["alpha"], ["en"])
    except Exception:
        pass

    c = TestClient(app)
    c.headers.update({"Origin": "http://testserver"})
    c.post("/login", data={"username": "admin", "password": "pw"})
    user = auth.authenticate("admin", "pw")
    if user:
        token = auth.create_api_token(user.id)
        c.headers.update({"Authorization": f"Bearer {token}"})
    return c


@pytest.fixture
def client_no_auth():
    from web.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def client_logged_in(
    isolated_users_db,
    isolated_packs_json,
    isolated_output_tree,
    isolated_qaps,
):
    from web.app import app
    from web import auth
    from web.db import init_app_db
    init_app_db()
    try:
        auth.create_user("admin", "pw", ["alpha"], ["en"])
    except Exception:
        pass
    with TestClient(app) as c:
        c.headers.update({"Origin": "http://testserver"})
        r = c.post("/login", data={"username": "admin", "password": "pw"}, follow_redirects=False)
        assert r.status_code == 303
        yield c


