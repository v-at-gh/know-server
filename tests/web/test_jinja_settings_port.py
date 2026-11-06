"""Tests for Jinja settings port (C.2 unified UI)."""

def test_profile_page_renders_all_sections(admin_client):
    """Full settings form renders: domains, langs, favorites, generation, overrides."""
    r = admin_client.get("/settings/profile")
    assert r.status_code == 200
    assert "articles_per_domain" in r.text
    assert "Favorites" in r.text
    assert "Per-Domain Overrides" in r.text
    assert "Per-Language Overrides" in r.text


def test_profile_post_full_update(admin_client):
    """POST with all fields persists and redirects."""
    r = admin_client.post("/settings/profile", data={
        "active_domains": "alpha",
        "active_langs": "en",
        "favorites": "alpha",
        "articles_per_domain": "15",
        "select_questions": "8",
        "construct_questions": "2",
        "per_domain_slug": "alpha",
        "per_domain_articles": "12",
        "per_domain_select": "4",
        "per_domain_construct": "1",
        "per_lang_code": "ru",
        "per_lang_translate_domains": "alpha",
    }, follow_redirects=False)
    assert r.status_code == 303
    # Verify persisted via GET
    r2 = admin_client.get("/api/v1/users/me/settings")
    data = r2.json()
    assert data["generation_params"]["articles_per_domain"] == 15
    assert data["generation_params"]["per_domain"]["alpha"]["articles_per_domain"] == 12


def test_profile_post_invalid_returns_400_with_error(admin_client):
    """Invalid articles_per_domain (e.g. 100) returns 400, not 500."""
    r = admin_client.post("/settings/profile", data={
        "active_domains": "alpha",
        "active_langs": "en",
        "articles_per_domain": "100",  # invalid, > 30
        "select_questions": "5",
        "construct_questions": "0",
    })
    assert r.status_code == 400
    assert "articles_per_domain" in r.text


def test_profile_post_pruned_favorites_banner(admin_client):
    """Favorites not in active_domains get pruned and shown in banner."""
    admin_client.put("/api/v1/users/me/settings", json={
        "active_domains": ["alpha", "beta"],
        "favorites": ["alpha", "beta"],
    })
    r = admin_client.post("/settings/profile", data={
        "active_domains": "alpha",
        "active_langs": "en",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "pruned=beta" in r.headers["location"]


def test_profile_post_clears_all_favorites(admin_client):
    """User unchecks all favorites; submit clears favorites."""
    admin_client.put("/api/v1/users/me/settings", json={
        "active_domains": ["alpha"],
        "favorites": ["alpha"],
    })
    r = admin_client.post("/settings/profile", data={
        "_favorites_section": "1",
        "active_domains": "alpha",
        "active_langs": "en",
        "articles_per_domain": "10",
        "select_questions": "5",
        "construct_questions": "0",
    }, follow_redirects=False)
    assert r.status_code == 303
    r2 = admin_client.get("/api/v1/users/me/settings")
    assert r2.json()["favorites"] == []


def test_profile_post_preserves_favorites_without_marker(admin_client):
    """Old form (no marker) preserves existing favorites (backward compat)."""
    admin_client.put("/api/v1/users/me/settings", json={
        "active_domains": ["alpha"],
        "favorites": ["alpha"],
    })
    r = admin_client.post("/settings/profile", data={
        "active_domains": "alpha",
        "active_langs": "en",
        "articles_per_domain": "10",
        "select_questions": "5",
        "construct_questions": "0",
    }, follow_redirects=False)
    assert r.status_code == 303
    r2 = admin_client.get("/api/v1/users/me/settings")
    assert r2.json()["favorites"] == ["alpha"]


def test_profile_post_questions_types_sum_violation(admin_client):
    """Sum of select + construct > 20 returns 400."""
    r = admin_client.post("/settings/profile", data={
        "active_domains": "alpha",
        "active_langs": "en",
        "articles_per_domain": "10",
        "select_questions": "15",
        "construct_questions": "10",  # sum = 25, > 20
    })
    assert r.status_code == 400
    assert "sum" in r.text.lower() or "questions_types" in r.text


def test_parse_settings_form_helper():
    """_parse_settings_form handles empty, simple, nested cases."""
    from web.app import _parse_settings_form
    class DummyForm:
        def __init__(self, data):
            self.data = data
        def getlist(self, key):
            val = self.data.get(key, [])
            return val if isinstance(val, list) else [val]
        def get(self, key, default=""):
            return self.data.get(key, default)

    form = DummyForm({
        "active_domains": ["alpha"],
        "active_langs": ["en"],
        "favorites": ["alpha"],
        "articles_per_domain": "12",
        "select_questions": "6",
        "construct_questions": "1",
    })
    patch = _parse_settings_form(form)
    assert patch["active_domains"] == ["alpha"]
    assert patch["generation_params"]["articles_per_domain"] == 12
