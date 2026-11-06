"""Spec CRUD unit tests. Isolated packs.json via fixture."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def spec_file(tmp_path, monkeypatch):
    """Point kb_packs.PACKS_JSON at temp file."""
    from quizkb import packs as kb_packs
    import quizkb.config as config
    target = tmp_path / "packs.json"
    monkeypatch.setattr("quizkb.packs.PACKS_JSON", target, raising=False)
    monkeypatch.setattr(config, "PACKS_JSON", target)
    return target


def test_load_empty_when_missing(spec_file):
    from quizkb import packs as kb_packs
    spec = kb_packs.load_spec()
    assert spec["packs"] == []
    assert spec["schema_version"] == kb_packs.SPEC_SCHEMA_VERSION


def test_load_raises_on_corrupt_json(spec_file):
    spec_file.write_text("{invalid", encoding="utf-8")
    from quizkb import packs as kb_packs
    with pytest.raises(ValueError, match="corrupt"):
        kb_packs.load_spec()


def test_create_pack_persists_with_empty_lists(spec_file):
    from quizkb import packs as kb_packs
    created = kb_packs.create_pack({
        "id": "test-pack",
        "kind": "campaign",
        "domains": ["linux"],
        "languages": ["en"],
        # campaigns not provided
    })
    assert created["campaigns"] == []  # normalized
    spec = kb_packs.load_spec()
    assert len(spec["packs"]) == 1
    assert spec["packs"][0]["campaigns"] == []
    assert spec["packs"][0]["domains"] == ["linux"]


def test_create_rejects_duplicate_id(spec_file):
    from quizkb import packs as kb_packs
    kb_packs.create_pack({"id": "dup", "kind": "campaign", "domains": [], "languages": []})
    with pytest.raises(ValueError, match="already exists"):
        kb_packs.create_pack({"id": "dup", "kind": "campaign", "domains": [], "languages": []})


def test_create_rejects_invalid_id_format(spec_file):
    from quizkb import packs as kb_packs
    with pytest.raises(ValueError, match="invalid pack id"):
        kb_packs.create_pack({"id": "Invalid ID!", "kind": "campaign", "domains": [], "languages": []})


def test_create_rejects_unknown_language(spec_file):
    from quizkb import packs as kb_packs
    with pytest.raises(ValueError, match="not in configured langs"):
        kb_packs.create_pack({
            "id": "x", "kind": "campaign", "domains": [], "languages": ["zz"]
        })


def test_update_replaces_fields(spec_file):
    from quizkb import packs as kb_packs
    kb_packs.create_pack({
        "id": "u1", "kind": "campaign",
        "domains": ["a", "b"], "languages": ["en"], "campaigns": ["x"]
    })
    updated = kb_packs.update_pack("u1", {
        "domains": ["c"],
        "languages": ["en", "ru"],
        # campaigns not provided → preserved
    })
    assert updated["domains"] == ["c"]
    assert updated["languages"] == ["en", "ru"]
    assert updated["campaigns"] == ["x"]  # preserved


def test_update_clears_on_empty_list(spec_file):
    from quizkb import packs as kb_packs
    kb_packs.create_pack({
        "id": "u2", "kind": "campaign",
        "domains": ["a"], "languages": ["en"], "campaigns": ["x"]
    })
    updated = kb_packs.update_pack("u2", {"campaigns": []})
    assert updated["campaigns"] == []


def test_update_rejects_unknown_id(spec_file):
    from quizkb import packs as kb_packs
    with pytest.raises(ValueError, match="not found"):
        kb_packs.update_pack("missing", {"kind": "campaign"})


def test_delete_returns_true_when_found(spec_file):
    from quizkb import packs as kb_packs
    kb_packs.create_pack({"id": "d1", "kind": "campaign", "domains": [], "languages": []})
    assert kb_packs.delete_pack("d1") is True
    assert kb_packs.find_spec_pack("d1") is None


def test_delete_returns_false_when_absent(spec_file):
    from quizkb import packs as kb_packs
    assert kb_packs.delete_pack("nonexistent") is False


def test_schema_version_written(spec_file):
    from quizkb import packs as kb_packs
    kb_packs.create_pack({"id": "sv", "kind": "campaign", "domains": [], "languages": []})
    raw = json.loads(spec_file.read_text())
    assert raw["schema_version"] == kb_packs.SPEC_SCHEMA_VERSION


def test_unknown_fields_preserved(spec_file):
    """Forward-compat: C.3-B adds `selection` field, must round-trip."""
    from quizkb import packs as kb_packs
    kb_packs.create_pack({
        "id": "fc", "kind": "campaign", "domains": ["A"], "languages": [],
        "selection": {"A": ["art-1"]},  # future field
    })
    spec = kb_packs.load_spec()
    entry = kb_packs.find_spec_pack("fc")
    assert entry["selection"] == {"A": ["art-1"]}
