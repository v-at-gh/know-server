"""Jinja pack routes test suite for GET/POST /packs/builder, /packs/{id}/delete, /packs/build."""

from urllib.parse import unquote
import pytest
from tests.fixtures_pack_web import (
    make_valid_pack_def,
    make_valid_builder_form,
    make_invalid_pack_defs,
)


# --- C.1 GET /packs/builder ---

def test_builder_empty_spec_renders_form(admin_client):
    r = admin_client.get("/packs/builder")
    assert r.status_code == 200
    assert 'name="id"' in r.text


def test_builder_edit_existing_pack(admin_client):
    admin_client.post("/api/v1/packs", json=make_valid_pack_def("existing-pack"))
    r = admin_client.get("/packs/builder?edit=existing-pack")
    assert r.status_code == 200
    assert "existing-pack" in r.text


def test_builder_edit_nonexistent_pack(admin_client):
    r = admin_client.get("/packs/builder?edit=does-not-exist")
    assert r.status_code == 200
    assert 'name="id"' in r.text


# --- C.2 POST /packs/builder ---

def test_builder_create_success(admin_client):
    form = make_valid_builder_form("new-pack")
    r = admin_client.post("/packs/builder", data=form, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/packs")
    assert "?error=" not in r.headers["location"]


def test_builder_create_empty_id(admin_client):
    r = admin_client.post(
        "/packs/builder",
        data={"id": "   ", "kind": "campaign", "domains": "alpha", "languages": "en"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/packs/builder?error=")
    err = unquote(loc.split("error=")[1])
    assert "empty_id" in err


def test_builder_create_missing_id_422(admin_client):
    r = admin_client.post("/packs/builder", data={"kind": "campaign"}, follow_redirects=False)
    assert r.status_code == 422


@pytest.mark.parametrize("case_name", list(make_invalid_pack_defs().keys()))
def test_create_invalid_defs(admin_client, case_name):
    pack_dict, expected_substring = make_invalid_pack_defs()[case_name]
    r = admin_client.post("/api/v1/packs", json=pack_dict)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert expected_substring.lower() in detail.lower()


def test_builder_create_invalid_kind(admin_client):
    pack_dict, expected_substring = make_invalid_pack_defs()["bad_kind"]
    form = make_valid_builder_form("bad-kind-pack")
    form["kind"] = pack_dict["kind"]
    r = admin_client.post("/packs/builder", data=form, follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "?error=" in loc
    assert expected_substring.lower() in unquote(loc).lower()


def test_builder_create_duplicate_id(admin_client):
    admin_client.post("/api/v1/packs", json=make_valid_pack_def("dup-pack"))
    form = make_valid_builder_form("dup-pack")
    form["domains"] = ["beta"]
    r = admin_client.post("/packs/builder", data=form, follow_redirects=False)
    assert r.status_code == 303
    assert "?error=" not in r.headers["location"]


# --- C.3 POST /packs/{pack_id}/delete ---

def test_pack_delete_existing_with_artifact(admin_client, tmp_path, monkeypatch, subprocess_spy):
    admin_client.post("/api/v1/packs", json=make_valid_pack_def("del-pack"))
    
    import quizkb.config as config
    qaps_dir = tmp_path / "qaps"
    qaps_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "QAPS_DIR", qaps_dir)
    monkeypatch.setattr("quizkb.packs.QAPS_DIR", qaps_dir, raising=False)
    qap_file = qaps_dir / "del-pack.qap"
    qap_file.write_bytes(b"fake")

    subprocess_spy["returncode"] = 0
    r = admin_client.post("/packs/del-pack/delete", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/packs")
    assert not qap_file.exists()
    assert len(subprocess_spy["calls"]) >= 1


def test_pack_delete_existing_no_artifact(admin_client, subprocess_spy):
    admin_client.post("/api/v1/packs", json=make_valid_pack_def("del-no-art"))
    subprocess_spy["returncode"] = 0
    r = admin_client.post("/packs/del-no-art/delete", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].endswith("/packs")


def test_pack_delete_nonexistent(admin_client, subprocess_spy):
    r = admin_client.post("/packs/nonexistent/delete", follow_redirects=False)
    assert r.status_code == 404
    data = r.json()
    assert data["status"] == "error"
    assert len(subprocess_spy["calls"]) == 0


def test_pack_delete_subprocess_fails(admin_client, subprocess_spy):
    admin_client.post("/api/v1/packs", json=make_valid_pack_def("sub-fail"))
    subprocess_spy["raises"] = Exception("simulated update_manifest failure")
    r = admin_client.post("/packs/sub-fail/delete", follow_redirects=False)
    assert r.status_code == 303
    # Spec entry still removed
    r_check = admin_client.get("/api/v1/packs/sub-fail")
    assert r_check.status_code == 404


# --- C.4 POST /packs/build ---

def test_packs_build_success(admin_client, subprocess_spy):
    subprocess_spy["returncode"] = 0
    subprocess_spy["stdout"] = "build ok"
    r = admin_client.post("/packs/build", data={"pack_id": "test-pack"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert len(subprocess_spy["calls"]) == 2


def test_packs_build_script_failure(admin_client, subprocess_spy):
    subprocess_spy["returncode"] = 1
    subprocess_spy["stderr"] = "build error"
    r = admin_client.post("/packs/build", data={"pack_id": "test-pack"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "error"


def test_packs_build_missing_script(admin_client, tmp_path, monkeypatch, subprocess_spy):
    import web.app as web_app
    monkeypatch.setattr(web_app, "REPO_ROOT", tmp_path)
    r = admin_client.post("/packs/build", data={"pack_id": "test-pack"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "error"
    assert "Missing" in data["message"]
    assert len(subprocess_spy["calls"]) == 0


def test_packs_build_exception(admin_client, subprocess_spy):
    subprocess_spy["raises"] = Exception("boom")
    r = admin_client.post("/packs/build", data={"pack_id": "test-pack"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "error"
    assert "boom" in data["message"]
