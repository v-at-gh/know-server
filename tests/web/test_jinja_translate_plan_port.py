"""Tests for Jinja translation plan preview and enqueue port."""

def test_translate_plan_preview_renders(admin_client, monkeypatch):
    """GET plan page renders table with items."""
    from web import app as web_app
    def fake_plan(pattern, langs, cache=None, user_id=None, scope="all"):
        return [
            {"slug": "alpha", "lang": "ru", "action": "queue",
             "reason": "missing", "state": "missing"},
            {"slug": "alpha", "lang": "uk", "action": "skip",
             "reason": "already translated", "state": "bootstrapped"},
        ]
    monkeypatch.setattr(web_app, "_compute_translate_plan", fake_plan)
    r = admin_client.get("/domain/en/alpha/translate/plan?langs=ru,uk")
    assert r.status_code == 200
    assert "ru" in r.text
    assert "uk" in r.text
    assert "queue" in r.text.lower()


def test_translate_plan_preview_no_enqueue(admin_client, monkeypatch):
    """GET preview must not call orchestrator."""
    orch_calls = []
    async def tracking_post(*a, **kw):
        orch_calls.append(a)
        return None
    monkeypatch.setattr("web.app.orch_post", tracking_post)
    admin_client.get("/domain/en/alpha/translate/plan?langs=ru")
    assert len(orch_calls) == 0


def test_translate_enqueue_queues_all(admin_client, monkeypatch):
    """POST enqueue calls orchestrator per queue item."""
    from web import app as web_app
    def fake_plan(*a, **kw):
        return [
            {"slug": "alpha", "lang": "ru", "action": "queue", "reason": "missing", "state": "missing"},
            {"slug": "alpha", "lang": "uk", "action": "skip", "reason": "done", "state": "bootstrapped"},
        ]
    monkeypatch.setattr(web_app, "_compute_translate_plan", fake_plan)
    orch_calls = []
    async def tracking_post(path, json_data=None, request=None, timeout=5.0):
        orch_calls.append((path, json_data))
        class Resp:
            status_code = 201
            def json(self): return {"task_id": "abc"}
        return Resp()
    monkeypatch.setattr("web.app.orch_post", tracking_post)
    r = admin_client.post("/domain/en/alpha/translate/enqueue",
                          data={"langs": "ru,uk"}, follow_redirects=False)
    assert r.status_code == 303
    assert len(orch_calls) == 1  # only queue item, skip was skipped
    assert orch_calls[0][1]["lang"] == "ru"


def test_translate_enqueue_orchestrator_offline(admin_client, monkeypatch):
    """POST enqueue handles orchestrator None response gracefully."""
    from web import app as web_app
    def fake_plan(*a, **kw):
        return [{"slug": "alpha", "lang": "ru", "action": "queue", "reason": "missing", "state": "missing"}]
    monkeypatch.setattr(web_app, "_compute_translate_plan", fake_plan)
    async def offline_post(*a, **kw):
        return None
    monkeypatch.setattr("web.app.orch_post", offline_post)
    r = admin_client.post("/domain/en/alpha/translate/enqueue",
                          data={"langs": "ru"}, follow_redirects=False)
    assert r.status_code == 303
    assert "error=orchestrator_offline" in r.headers["location"]
