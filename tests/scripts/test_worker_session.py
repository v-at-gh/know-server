"""Boundary tests for worker HTTP session reuse singleton and adapter config."""

def test_worker_session_singleton():
    """_get_session returns same instance on repeated calls."""
    import scripts.worker as worker_mod
    worker_mod._worker_session = None  # reset
    s1 = worker_mod._get_session()
    s2 = worker_mod._get_session()
    assert s1 is s2
    worker_mod._worker_session = None

def test_worker_session_adapter_configured():
    """Session has HTTPAdapter with correct pool settings."""
    import scripts.worker as worker_mod
    worker_mod._worker_session = None
    s = worker_mod._get_session()
    adapter = s.get_adapter("http://example")
    assert adapter._pool_connections == 2
    assert adapter._pool_maxsize == 4
    assert adapter.max_retries.total == 0  # type: ignore
    worker_mod._worker_session = None
