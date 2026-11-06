"""Schema tests for selection field in pack definitions."""

import json
import pytest


@pytest.fixture
def spec_file(tmp_path, monkeypatch):
    import quizkb.config as config
    target = tmp_path / "packs.json"
    monkeypatch.setattr("quizkb.packs.PACKS_JSON", target, raising=False)
    monkeypatch.setattr(config, "PACKS_JSON", target)
    return target


def test_create_pack_with_selection(spec_file):
    from quizkb import packs as kb_packs
    created = kb_packs.create_pack({
        "id": "mini-a", "kind": "custom",
        "domains": ["A", "B"],
        "languages": ["en"],
        "selection": {
            "A": {"articles": ["art-1", "art-5"], "questions_per_article": 10},
        },
    })
    assert created["selection"] == {
        "A": {"articles": ["art-1", "art-5"], "questions_per_article": 10},
    }


def test_create_without_selection(spec_file):
    from quizkb import packs as kb_packs
    created = kb_packs.create_pack({
        "id": "full", "kind": "campaign",
        "domains": ["A"], "languages": ["en"],
    })
    assert "selection" not in created


def test_selection_key_must_be_in_domains(spec_file):
    from quizkb import packs as kb_packs
    with pytest.raises(ValueError, match="not in pack domains"):
        kb_packs.create_pack({
            "id": "bad", "kind": "custom",
            "domains": ["A"], "languages": ["en"],
            "selection": {"B": {"articles": ["x"], "questions_per_article": 5}},
        })


def test_selection_qpa_must_be_positive_int(spec_file):
    from quizkb import packs as kb_packs
    with pytest.raises(ValueError, match="questions_per_article"):
        kb_packs.create_pack({
            "id": "bad", "kind": "custom",
            "domains": ["A"], "languages": ["en"],
            "selection": {"A": {"articles": ["x"], "questions_per_article": 0}},
        })


def test_selection_articles_must_be_strings(spec_file):
    from quizkb import packs as kb_packs
    with pytest.raises(ValueError, match="invalid slug"):
        kb_packs.create_pack({
            "id": "bad", "kind": "custom",
            "domains": ["A"], "languages": ["en"],
            "selection": {"A": {"articles": [123], "questions_per_article": 5}},
        })
