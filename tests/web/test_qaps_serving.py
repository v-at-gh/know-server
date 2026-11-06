def test_qaps_file_served(admin_client, isolated_qaps):
    qaps_dir, _ = isolated_qaps
    qaps_dir.mkdir(parents=True, exist_ok=True)
    test_file = qaps_dir / "test.qap"
    test_file.write_bytes(b"fake qap content")

    r = admin_client.get("/qaps/test.qap")
    assert r.status_code == 200
    assert r.content == b"fake qap content"


def test_qaps_file_404_when_missing(admin_client, isolated_qaps):
    r = admin_client.get("/qaps/nonexistent.qap")
    assert r.status_code == 404


def test_qaps_file_rejects_traversal(admin_client):
    r = admin_client.get("/qaps/../etc/passwd")
    assert r.status_code in (400, 404)


def test_qaps_file_requires_qap_extension(admin_client, isolated_qaps):
    qaps_dir, _ = isolated_qaps
    qaps_dir.mkdir(parents=True, exist_ok=True)
    (qaps_dir / "test.txt").write_bytes(b"not a qap")
    r = admin_client.get("/qaps/test.txt")
    assert r.status_code == 400
