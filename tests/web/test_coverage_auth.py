def test_coverage_table_accepts_bearer(client_no_auth, isolated_users_db):
    """Bearer token (without session cookie) works for /api/coverage/table."""
    from web import auth
    auth.init_db()
    try:
        auth.create_user("admin", "pw", ["alpha"], ["en"])
    except Exception:
        pass
    user = auth.authenticate("admin", "pw")
    token = auth.create_api_token(user.id)
    r = client_no_auth.get("/api/coverage/table?langs=en", headers={"Authorization": f"Bearer {token}"}, follow_redirects=False)
    assert r.status_code == 200
    assert "<table" in r.text or "<tr>" in r.text or "ready" in r.text


def test_coverage_table_rejects_no_auth(client_no_auth):
    """No auth → 401 (Bearer-or-session helper returns 401)."""
    r = client_no_auth.get("/api/coverage/table?langs=en")
    assert r.status_code == 401


def test_coverage_table_accepts_session(client_logged_in):
    """Session cookie works for /api/coverage/table."""
    r = client_logged_in.get("/api/coverage/table?langs=en")
    assert r.status_code == 200
