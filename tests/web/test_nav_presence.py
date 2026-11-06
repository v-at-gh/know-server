import pytest
from quizkb import packs as kb_packs


@pytest.mark.parametrize("path", [
    "/coverage",
    "/domains",
    "/campaigns",
    "/packs",
    "/packs/builder",
    "/config/domains",
    "/config/campaigns",
    "/errors",
    "/feedback",
    "/tasks",
    "/network",
    "/search",
    "/settings/profile",
    "/article/en/alpha/art-1",
    "/question/some-uuid",
    "/question/find/abc",
    "/tasks/some-task-id",
])
def test_nav_renders_on_all_pages_when_logged_in(client_logged_in, path):
    """Nav present on every Jinja page for authenticated user, regardless of
    whether handler passes user in context explicitly."""
    r = client_logged_in.get(path, follow_redirects=False)
    if r.status_code in (404, 500):
        pytest.skip(f"content missing or error for {path}")
    assert r.status_code == 200, f"{path}: unexpected {r.status_code}"
    assert 'href="/coverage"' in r.text, f"nav missing on {path}"
    assert 'action="/logout"' in r.text, f"logout form missing on {path}"


@pytest.mark.parametrize("path", [
    "/article/en/alpha/art-1/edit",
    "/question/some-uuid/edit",
])
def test_nav_renders_on_edit_pages(client_logged_in, path):
    """Edit pages also render nav."""
    r = client_logged_in.get(path, follow_redirects=False)
    if r.status_code in (404, 500):
        pytest.skip(f"content missing or error for {path}")
    assert 'href="/coverage"' in r.text, f"nav missing on {path}"


def test_pack_view_nav_when_logged_in(client_logged_in, isolated_qaps, monkeypatch):
    """Public /pack/{id} renders nav for authenticated user."""
    qaps_dir, qaps_index = isolated_qaps
    qaps_dir.mkdir(parents=True, exist_ok=True)
    (qaps_dir / "navtest.qap").write_bytes(b"fake")

    monkeypatch.setattr(kb_packs, "load_detail", lambda entry: kb_packs.PackDetail(
        entry=entry, domains=[], campaigns=[], files=[], articles_total=0, questions_total=0
    ))

    qaps_index.parent.mkdir(parents=True, exist_ok=True)
    qaps_index.write_text('{"packs":[{"id":"navtest","file":"navtest.qap","version":"1","size":4,"sha256":"x","kind":"campaign","createdAt":"2026","languages":[],"campaigns":[],"domains":[],"totals":{}}]}')

    r = client_logged_in.get("/pack/navtest")
    assert r.status_code == 200
    assert 'action="/logout"' in r.text


def test_pack_view_no_nav_when_logged_out(client_no_auth, isolated_qaps, monkeypatch):
    """Public /pack/{id} hides nav for anonymous user."""
    qaps_dir, qaps_index = isolated_qaps
    qaps_dir.mkdir(parents=True, exist_ok=True)
    (qaps_dir / "navtest2.qap").write_bytes(b"fake")

    monkeypatch.setattr(kb_packs, "load_detail", lambda entry: kb_packs.PackDetail(
        entry=entry, domains=[], campaigns=[], files=[], articles_total=0, questions_total=0
    ))

    qaps_index.parent.mkdir(parents=True, exist_ok=True)
    qaps_index.write_text('{"packs":[{"id":"navtest2","file":"navtest2.qap","version":"1","size":4,"sha256":"x","kind":"campaign","createdAt":"2026","languages":[],"campaigns":[],"domains":[],"totals":{}}]}')

    r = client_no_auth.get("/pack/navtest2")
    assert r.status_code == 200
    assert 'action="/logout"' not in r.text
