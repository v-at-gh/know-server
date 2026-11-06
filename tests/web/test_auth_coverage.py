import pytest

PROTECTED_HTML_GETS = [
    "/packs/builder",
    "/network",
    "/question/some-uuid",
    "/article/en/alpha/art-1",
    "/question/find/ab",
    "/tasks/some-task-id",
]

PROTECTED_POSTS = [
    ("/packs/build", {"pack_id": "x"}),
    ("/sync", {}),
    ("/domain/en/alpha/meta", {"name": "x"}),
    ("/issues/ack", {"key": "x"}),
    ("/issues/unack", {"key": "x"}),
    ("/network/ping", {"host": "127.0.0.1"}),
]

PROTECTED_API_GETS = [
    "/api/tasks/active",
    "/api/tasks/some-id",
]


@pytest.mark.parametrize("path", PROTECTED_HTML_GETS)
def test_protected_html_get_requires_auth(client_no_auth, path):
    """Unauthenticated GET → 303 redirect to /login."""
    r = client_no_auth.get(path, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == "/login"


@pytest.mark.parametrize("path,data", PROTECTED_POSTS)
def test_protected_post_requires_auth(client_no_auth, path, data):
    """Unauthenticated POST → 303 redirect to /login."""
    r = client_no_auth.post(path, data=data, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == "/login"


@pytest.mark.parametrize("path", PROTECTED_API_GETS)
def test_protected_api_get_returns_401(client_no_auth, path):
    """Unauthenticated API GET → 401 JSON (not HTML redirect)."""
    r = client_no_auth.get(path)
    assert r.status_code == 401
    data = r.json()
    assert "Authentication required" in data.get("detail", "")


def test_api_returns_401_for_invalid_bearer(client_no_auth):
    """Invalid Bearer → 401, not 303."""
    r = client_no_auth.get("/api/tasks/active", headers={"Authorization": "Bearer bogus"})
    assert r.status_code == 401


def test_public_endpoints_still_accessible(client_no_auth):
    """Confirm public endpoints unchanged."""
    assert client_no_auth.get("/qaps/manifest.json").status_code in (200, 404)
    assert client_no_auth.post("/api/v1/auth/token",
                                json={"username": "nonexistent", "password": "x"}
                                ).status_code == 401  # endpoint auth check, not middleware


def test_logout_get_returns_405(client_logged_in):
    """GET /logout no longer valid (CSRF risk) → 405."""
    r = client_logged_in.get("/logout", follow_redirects=False)
    assert r.status_code == 405


def test_logout_post_clears_session(client_logged_in):
    """POST /logout clears session, redirects to /login."""
    r = client_logged_in.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    # Verify session cleared
    r2 = client_logged_in.get("/coverage", follow_redirects=False)
    assert r2.status_code == 303
    assert r2.headers["location"] == "/login"


def test_nav_visible_when_logged_in(client_logged_in):
    """Authenticated: nav dropdowns and logout form present."""
    r = client_logged_in.get("/coverage")
    assert 'href="/coverage"' in r.text
    assert 'action="/logout"' in r.text
    assert "Logout" in r.text


def test_nav_hidden_when_logged_out(client_no_auth):
    """Unauthenticated /login: no nav dropdowns, no logout, no sync form."""
    r = client_no_auth.get("/login")
    assert r.status_code == 200
    assert 'action="/sync"' not in r.text
    assert 'action="/logout"' not in r.text
    # Nav content links absent
    assert 'href="/coverage"' not in r.text
    # Theme toggle still present (login page dark mode)
    assert 'id="theme-toggle"' in r.text

