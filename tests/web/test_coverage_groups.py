def test_group_toggle_all_none_renders(client_logged_in):
    """Each domain group has All/None links."""
    r = client_logged_in.get("/coverage")
    assert r.status_code == 200
    body = r.text
    assert 'toggleGroup' in body
