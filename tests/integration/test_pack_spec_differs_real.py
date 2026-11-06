"""Integration: _spec_differs_from_built with real .qap artifacts.

Prior tests used fake dict manifests, missing the DomainInPack branch.
This test builds a real .qap via build_qap, loads via kb_packs.load_index,
and verifies comparison works.
"""

import json
import pytest


@pytest.fixture
def real_qap(tmp_path, monkeypatch):
    """Build a real .qap in tmp_path, return (qaps_dir, manifest_json)."""
    import quizkb.config as config

    qaps_dir = tmp_path / "qaps"
    manifest_json = tmp_path / "manifest.json"
    qaps_dir.mkdir()

    monkeypatch.setattr(config, "QAPS_DIR", qaps_dir)
    monkeypatch.setattr(config, "QAPS_INDEX", manifest_json)
    monkeypatch.setattr("quizkb.packs.QAPS_DIR", qaps_dir, raising=False)
    monkeypatch.setattr("quizkb.packs.QAPS_INDEX", manifest_json, raising=False)

    # Build a staging tree
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "datasets" / "en").mkdir(parents=True)
    (staging / "datasets" / "en" / "domains.json").write_text(
        json.dumps([{"slug": "linux", "name": "Linux", "kind": "tool"}]),
        encoding="utf-8",
    )
    (staging / "datasets" / "en" / "linux.json").write_text(
        json.dumps([{"uuid": "q-1", "text": "Q1", "answer": "A1",
                     "relatedArticleSlug": "linux/art1"}]),
        encoding="utf-8",
    )
    (staging / "knowledge" / "en" / "linux").mkdir(parents=True)
    (staging / "knowledge" / "en" / "linux" / "art1.md").write_text(
        "# Art 1\n" + "x" * 2500, encoding="utf-8"
    )
    (staging / "knowledge" / "en" / "index.json").write_text(
        json.dumps([{"slug": "linux/art1", "uuid": "u-1",
                     "domainSlug": "linux", "title": "A1",
                     "path": "linux/art1.md", "order": 1,
                     "description": "S"}]),
        encoding="utf-8",
    )
    (staging / "manifest.json").write_text(
        json.dumps({"qapFormatVersion": 2, "packId": "test-pack",
                    "packVersion": "2026.10.31", "kind": "campaign"}),
        encoding="utf-8",
    )

    # Build real .qap
    from scripts import build_qap
    qap_path = qaps_dir / "test-pack.qap"
    build_qap.build(staging, qap_path, pack_id="test-pack",
                    pack_version="2026.10.31", kind="campaign")

    # Refresh manifest
    import importlib
    from scripts import update_manifest
    importlib.reload(update_manifest)
    update_manifest.main()

    return qaps_dir, manifest_json


def test_spec_differs_from_built_with_real_packentry(real_qap):
    """Real PackEntry loading and object/slug normalization in _spec_differs_from_built."""
    qaps_dir, manifest_json = real_qap

    from quizkb import packs as kb_packs
    index = kb_packs.load_index()
    assert len(index) >= 1
    entry = next(e for e in index if e.id == "test-pack")
    assert not isinstance(entry, dict)
    assert entry.domains

    # Spec that matches built artifact
    matching_spec = {
        "id": "test-pack", "kind": "campaign",
        "domains": ["linux"], "languages": ["en"], "campaigns": [],
    }
    from web.app import _spec_differs_from_built
    assert _spec_differs_from_built(matching_spec, entry) is False

    # Spec that diverged (removed domain)
    diverged_spec = {
        "id": "test-pack", "kind": "campaign",
        "domains": [], "languages": ["en"], "campaigns": [],
    }
    assert _spec_differs_from_built(diverged_spec, entry) is True

    # Test with mock DomainInPack object with .slug attribute
    class DummyDomain:
        def __init__(self, slug):
            self.slug = slug
    entry_with_objects = kb_packs.PackEntry(
        file="test.qap", size=100, sha256="abc", id="test", version="1",
        kind="campaign", createdAt="2026", languages=["en"], campaigns=[],
        domains=[DummyDomain("linux")], totals={}
    )
    assert _spec_differs_from_built(matching_spec, entry_with_objects) is False


def test_built_to_dict_normalizes_domains(real_qap):
    """_built_to_dict should return domains as list[str], matching spec shape."""
    qaps_dir, manifest_json = real_qap
    from quizkb import packs as kb_packs
    from web.app import _built_to_dict

    index = kb_packs.load_index()
    entry = next(e for e in index if e.id == "test-pack")
    serialized = _built_to_dict(entry)

    assert serialized is not None
    assert isinstance(serialized["domains"], list)
    assert all(isinstance(x, str) for x in serialized["domains"])
    assert "linux" in serialized["domains"]
    assert isinstance(serialized["domains_detail"], list)
