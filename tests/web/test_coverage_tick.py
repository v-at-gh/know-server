def test_coverage_html_no_htmx_include(client_logged_in):
    """hx-include removed from container (was broken selector)."""
    r = client_logged_in.get("/coverage")
    body = r.text
    assert "hx-include=\"[name='domains']" not in body
    assert "hx-include='[name=\"domains\"]" not in body


def test_coverage_container_has_no_hx_trigger(client_logged_in):
    """hx-trigger removed from coverage-table-container (was causing
    unfiltered fetch on page load that overwrote filtered server render)."""
    r = client_logged_in.get("/coverage")
    body = r.text
    start = body.find('id="coverage-table-container"')
    assert start >= 0
    end = body.find('>', start)
    container_tag = body[start:end]
    assert "hx-trigger" not in container_tag


def test_coverage_html_no_htmx_every_trigger(client_logged_in):
    """every 10s removed from container hx-trigger."""
    r = client_logged_in.get("/coverage")
    body = r.text
    start = body.find('id="coverage-table-container"')
    end = body.find('>', start)
    container_tag = body[start:end]
    assert "every 10s" not in container_tag


def test_coverage_alpine_has_setinterval(client_logged_in):
    """Alpine component uses setInterval for 10s refresh."""
    r = client_logged_in.get("/coverage")
    body = r.text
    assert "this._interval = setInterval" in body
    assert "10000" in body or "10 * 1000" in body or "1e4" in body


def test_coverage_alpine_init_calls_fetchtable(client_logged_in):
    """Alpine init() calls fetchTable() for deterministic initial sync."""
    r = client_logged_in.get("/coverage")
    body = r.text
    init_start = body.find("init() {")
    assert init_start >= 0
    init_block = body[init_start:init_start + 100]
    assert "this.fetchTable();" in init_block


def test_coverage_fetchtable_builds_params(client_logged_in):
    """fetchTable() includes langs and domains in URL."""
    r = client_logged_in.get("/coverage")
    body = r.text
    assert "searchParams" in body
