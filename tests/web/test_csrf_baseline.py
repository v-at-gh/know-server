def test_csrf_blocks_cross_origin_post(client_logged_in):
    """POST with session cookie but wrong Origin → 403."""
    # Override Origin for one request
    r = client_logged_in.post(
        "/settings/profile",
        data={"active_domains": "alpha", "active_langs": "en"},
        headers={"Origin": "http://evil.com"},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert "Origin" in r.json()["detail"]


def test_csrf_allows_same_origin_post(client_logged_in):
    """POST with matching Origin → 200/303."""
    r = client_logged_in.post(
        "/settings/profile",
        data={"active_domains": "alpha", "active_langs": "en"},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_csrf_allows_bearer_post_without_origin(admin_client):
    """Bearer request bypasses Origin check."""
    # admin_client already has Origin, but explicit test:
    r = admin_client.post(
        "/settings/profile",
        data={"active_domains": "alpha", "active_langs": "en"},
        headers={"Origin": ""},  # override to remove
        follow_redirects=False,
    )
    # Bearer bypass → 200/303, not 403
    assert r.status_code in (200, 303)
    assert r.status_code != 403


def test_csrf_allows_no_session_post(client_no_auth):
    """POST without session cookie → no Origin check (login path)."""
    r = client_no_auth.post(
        "/api/v1/auth/token",
        json={"username": "nonexistent", "password": "x"},
        headers={"Origin": ""},
    )
    # Endpoint returns 401 (auth failure), not 403 (middleware)
    assert r.status_code == 401


def test_csrf_blocks_no_origin_with_session(client_logged_in):
    """Session cookie + POST + no Origin → 403."""
    r = client_logged_in.post(
        "/settings/profile",
        data={"active_domains": "alpha", "active_langs": "en"},
        headers={"Origin": ""},
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert "Origin" in r.json()["detail"]
