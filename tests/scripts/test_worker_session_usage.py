"""Behavioral tests: worker call sites use _get_session, not top-level requests."""


def test_recovery_uses_session(monkeypatch, tmp_path):
    """Recovery/poll functions use _get_session(), not top-level requests.get."""
    direct_calls = []
    original_get = __import__("requests").get
    def tracking_get(*a, **kw):
        direct_calls.append(a)
        return original_get(*a, **kw)

    monkeypatch.setattr("requests.get", tracking_get)

    class MockSession:
        def get(self, url, **kw):
            return type("R", (), {"status_code": 200, "json": lambda: []})()

    monkeypatch.setattr("scripts.worker._get_session", lambda: MockSession())
    monkeypatch.setattr("scripts.worker.REPO_ROOT", tmp_path)
    (tmp_path / "state" / "tasks").mkdir(parents=True, exist_ok=True)

    from scripts.worker import _recover_running_tasks
    _recover_running_tasks("http://test:8002")

    assert len(direct_calls) == 0


def test_main_poll_uses_session(monkeypatch):
    """main() poll path uses _get_session().get, not requests.get."""
    session_calls = []
    direct_calls = []

    class MockSession:
        def get(self, url, **kw):
            session_calls.append(url)
            return type("R", (), {"status_code": 204, "text": ""})()

    original_get = __import__("requests").get
    def tracking_get(*a, **kw):
        direct_calls.append(a)
        return original_get(*a, **kw)

    monkeypatch.setattr("scripts.worker._get_session", lambda: MockSession())
    monkeypatch.setattr("requests.get", tracking_get)

    from scripts.worker import _poll_once
    _poll_once("http://test:8002")

    assert len(session_calls) == 1
    assert "/poll" in session_calls[0]
    assert len(direct_calls) == 0


def test_progress_uses_session(monkeypatch):
    """Progress reporting path uses _get_session().post."""
    session_calls = []
    class MockSession:
        def post(self, url, **kw):
            session_calls.append(url)
            return type("R", (), {"status_code": 200})()
        def get(self, url, **kw):
            return type("R", (), {"status_code": 204, "text": ""})()

    monkeypatch.setattr("scripts.worker._get_session", lambda: MockSession())

    from scripts.worker import _report_progress
    _report_progress("http://test", "task-123", "running")

    assert any("/progress" in url for url in session_calls)
