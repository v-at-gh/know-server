def test_coverage_page_has_auto_refresh_trigger(client_logged_in):
    """Coverage page has Alpine setInterval for 10s auto-refresh."""
    r = client_logged_in.get("/coverage")
    assert r.status_code == 200
    assert "setInterval" in r.text
    assert "10000" in r.text


def test_coverage_page_has_live_badge(client_logged_in):
    """Coverage page has Live indicator."""
    r = client_logged_in.get("/coverage")
    assert "Live (10s)" in r.text


def test_coverage_page_preserves_filters(client_logged_in):
    """Alpine fetchTable() builds URLSearchParams for filtering."""
    r = client_logged_in.get("/coverage")
    body = r.text
    assert "searchParams" in body
