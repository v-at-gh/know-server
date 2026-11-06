"""Boundary test for admin HTTP client pool configuration."""

def test_admin_client_pool_configured():
    """Verify lifespan http_client has explicit pool config without transport retries."""
    from pathlib import Path
    src = Path("web/app.py").read_text()
    assert "keepalive_expiry=60.0" in src
    assert "httpx.Limits" in src
    assert "AsyncHTTPTransport(retries=1)" not in src
