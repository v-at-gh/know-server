import pytest


@pytest.fixture(autouse=True)
def seed_slug_data(admin_client, isolated_campaigns, monkeypatch):
    from web.db import get_app_db
    with get_app_db() as conn:
        conn.execute("INSERT OR REPLACE INTO domains (slug, name, description, kind) VALUES ('docker', 'Docker', '', 'tool')")
        conn.execute("INSERT OR REPLACE INTO domains (slug, name, description, kind) VALUES ('kubernetes', 'Kubernetes', '', 'tool')")
        conn.execute("INSERT OR REPLACE INTO domains (slug, name, description, kind) VALUES ('alpha', 'Alpha', '', 'tool')")
        conn.execute("INSERT OR REPLACE INTO campaigns (slug, name, description, \"order\") VALUES ('devops', 'DevOps Campaign', '', 1)")
        conn.commit()

    test_campaigns = [
        {
            "slug": "devops",
            "name": "DevOps Campaign",
            "description": "DevOps",
            "order": 1,
            "domainSlugs": ["docker", "kubernetes"],
            "prerequisiteDomainSlugs": [],
            "prerequisiteCampaignSlugs": [],
        }
    ]
    monkeypatch.setattr("quizkb.config.CAMPAIGNS", test_campaigns)
    monkeypatch.setattr("quizkb.state.CAMPAIGNS", test_campaigns)


@pytest.mark.parametrize("pattern,expected_substr", [
    ("doc", "docker"),         # partial
    ("Docker", "docker"),      # case-insensitive
    ("DOC", "docker"),         # uppercase partial
    ("kuber", "kubernetes"),   # partial long slug
    ("doc*", "docker"),        # glob still works
])
def test_domains_pattern_substring_match(admin_client, pattern, expected_substr):
    r = admin_client.get(f"/api/v1/domains?pattern={pattern}&langs=en&only_active=false")
    slugs = [d["slug"] for d in r.json()["domains"]]
    assert any(expected_substr in s for s in slugs), f"pattern={pattern!r}"


def test_domains_pattern_exact_full_slug_still_works(admin_client):
    r = admin_client.get("/api/v1/domains?pattern=docker&langs=en&only_active=false")
    assert "docker" in [d["slug"] for d in r.json()["domains"]]


def test_domains_pattern_no_match(admin_client):
    r = admin_client.get("/api/v1/domains?pattern=zzz&langs=en&only_active=false")
    assert r.json()["total"] == 0


def test_domains_no_pattern_returns_all(admin_client):
    r1 = admin_client.get("/api/v1/domains?langs=en&only_active=false")
    r2 = admin_client.get("/api/v1/domains?pattern=&langs=en&only_active=false")
    assert r1.json()["total"] == r2.json()["total"]


@pytest.mark.parametrize("pattern", ["devo", "DevOps", "DEVOPS", "devo*"])
def test_campaigns_pattern_substring_match(admin_client, pattern):
    r = admin_client.get(f"/api/v1/campaigns?pattern={pattern}&langs=en")
    slugs = [c["slug"] for c in r.json()["campaigns"]]
    assert any("devops" in s for s in slugs), f"pattern={pattern!r}"


def test_campaigns_no_pattern_returns_all(admin_client):
    r1 = admin_client.get("/api/v1/campaigns?langs=en")
    r2 = admin_client.get("/api/v1/campaigns?pattern=&langs=en")
    assert r1.json()["total"] == r2.json()["total"]
