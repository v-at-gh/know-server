# def test_htmx_served_from_vendor(admin_client):
#     r = admin_client.get("/static/vendor/htmx.min.js")
#     assert r.status_code == 200
#     assert "htmx" in r.text.lower()


# def test_no_external_cdn_in_base_template():
#     from pathlib import Path
#     base = Path("web/templates/base.html").read_text()
#     assert "unpkg.com" not in base
