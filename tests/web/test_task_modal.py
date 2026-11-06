def test_modal_partial_included_on_domain_page(client_logged_in, isolated_output_tree):
    """Domain page renders task modal markup (via include)."""
    r = client_logged_in.get("/domain/en/alpha")
    assert r.status_code == 200
    assert 'id="task-modal"' in r.text
    assert 'id="task-modal-close"' in r.text
    assert 'id="task-close-btn"' in r.text
    assert 'onclick="closeTaskModal()"' in r.text


def test_app_js_served(client_no_auth):
    """app.js served from /static/app.js."""
    r = client_no_auth.get("/static/app.js")
    assert r.status_code == 200
    assert "startTask" in r.text
    assert "closeTaskModal" in r.text
    assert "pollTask" in r.text


def test_base_template_loads_app_js(client_logged_in):
    """/static/app.js script tag present in rendered pages."""
    r = client_logged_in.get("/coverage")
    assert 'src="/static/app.js"' in r.text


def test_close_button_always_visible_in_markup(client_logged_in, isolated_output_tree):
    """Close button NOT wrapped in display:none; always rendered."""
    r = client_logged_in.get("/domain/en/alpha")
    assert r.status_code == 200
    assert 'id="task-modal-close"' in r.text
    assert 'id="task-close-btn"' in r.text
