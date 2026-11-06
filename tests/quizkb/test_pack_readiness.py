"""compute_pack_readiness unit tests."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def tree(tmp_path):
    out = tmp_path / "output"
    (out / "syllabus" / "en").mkdir(parents=True)
    (out / "syllabus" / "en" / "A.json").write_text(json.dumps([
        {"order": 1, "slug": "art-1", "title": "Art 1", "summary": "S", "uuid": "A-u1"},
        {"order": 2, "slug": "art-2", "title": "Art 2", "summary": "S", "uuid": "A-u2"},
    ]), encoding="utf-8")
    (out / "knowledge" / "en" / "A").mkdir(parents=True)
    for slug in ("art-1", "art-2"):
        (out / "knowledge" / "en" / "A" / f"{slug}.md").write_text(
            "# A\n" + "x" * 2500, encoding="utf-8"
        )
    (out / "datasets" / "en").mkdir(parents=True)
    qs = [{"uuid": f"q-{i}", "text": "Q", "answer": "A",
           "relatedArticleSlug": "A/art-1"} for i in range(5)]
    (out / "datasets" / "en" / "A.json").write_text(json.dumps(qs), encoding="utf-8")
    (out / "datasets" / "en" / "domains.json").write_text(json.dumps([
        {"slug": "A", "name": "A", "kind": "tool"},
    ]), encoding="utf-8")
    (out / "knowledge" / "en" / "index.json").write_text(json.dumps([
        {"slug": f"A/{s}", "uuid": f"A-u{i+1}", "domainSlug": "A",
         "title": s, "path": f"A/{s}.md", "order": i+1, "description": "S"}
        for i, s in enumerate(("art-1", "art-2"))
    ]), encoding="utf-8")
    return out


def test_readiness_per_article_detail(tree):
    from quizkb.packs import compute_pack_readiness
    pack_def = {
        "id": "test", "kind": "custom",
        "domains": ["A"], "languages": ["en"],
        "selection": {
            "A": {"articles": ["art-1", "art-2"], "questions_per_article": 5},
        },
    }
    result = compute_pack_readiness(pack_def, tree)
    assert result["pack_id"] == "test"
    assert result["has_selection"] is True
    assert result["ready"] is False
    assert "A" in result["per_domain"]
    a = result["per_domain"]["A"]
    assert a["articles_selected"] == 2
    detail = a["per_lang"]["en"]["articles"]
    art1 = next(d for d in detail if d["slug"] == "art-1")
    art2 = next(d for d in detail if d["slug"] == "art-2")
    assert art1["present"] is True
    assert art1["questions_current"] == 5
    assert art1["questions_target"] == 5
    assert art1["complete"] is True
    assert art2["present"] is True
    assert art2["questions_current"] == 0
    assert art2["complete"] is False


def test_readiness_no_selection_returns_empty_per_domain(tree):
    from quizkb.packs import compute_pack_readiness
    pack_def = {
        "id": "full", "kind": "campaign",
        "domains": ["A"], "languages": ["en"],
    }
    result = compute_pack_readiness(pack_def, tree)
    assert result["has_selection"] is False
    assert result["ready"] is True
    assert result["per_domain"] == {}


def test_readiness_missing_article_recorded(tree):
    from quizkb.packs import compute_pack_readiness
    pack_def = {
        "id": "test", "kind": "custom",
        "domains": ["A"], "languages": ["en"],
        "selection": {
            "A": {"articles": ["art-1", "art-99"], "questions_per_article": 5},
        },
    }
    result = compute_pack_readiness(pack_def, tree)
    detail = result["per_domain"]["A"]["per_lang"]["en"]["articles"]
    art99 = next(d for d in detail if d["slug"] == "art-99")
    assert art99["present"] is False
    assert art99["complete"] is False
