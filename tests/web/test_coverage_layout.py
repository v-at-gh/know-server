def test_coverage_table_rows_are_domains(client_logged_in, isolated_output_tree):
    """Transposed: rows are domains, columns are languages."""
    from web.db import get_app_db
    with get_app_db() as conn:
        conn.execute("INSERT OR REPLACE INTO domains (slug, name, description, kind) VALUES ('alpha', 'Alpha', '', 'tool')")
        conn.commit()

    r = client_logged_in.get("/api/coverage/table?langs=en&domains=alpha")
    assert r.status_code == 200
    body = r.text
    assert 'domain-row-name' in body
    thead = body.split("<thead>")[1].split("</thead>")[0] if "<thead>" in body else ""
    assert "en" in thead
