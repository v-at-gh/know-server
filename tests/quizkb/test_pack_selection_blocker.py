"""check_pack selection blocker tests."""

import json
import pytest


@pytest.fixture
def tree(tmp_path):
    out = tmp_path / "output"
    for ds in ("A",):
        (out / "syllabus" / "en" / f"{ds}.json").parent.mkdir(parents=True, exist_ok=True)
        (out / "syllabus" / "en" / f"{ds}.json").write_text(json.dumps([
            {"order": 1, "slug": "art-1", "title": "Art 1", "summary": "S", "uuid": f"{ds}-u1"},
            {"order": 2, "slug": "art-2", "title": "Art 2", "summary": "S", "uuid": f"{ds}-u2"},
        ]), encoding="utf-8")
        (out / "knowledge" / "en" / ds).mkdir(parents=True, exist_ok=True)
        for slug in ("art-1", "art-2"):
            (out / "knowledge" / "en" / ds / f"{slug}.md").write_text(
                "# Art\n" + "x" * 2500, encoding="utf-8"
            )
        qs = []
        for slug in ("art-1", "art-2"):
            for i in range(5):
                qs.append({
                    "uuid": f"{ds}-{slug}-q{i}",
                    "text": f"Q", "answer": f"A",
                    "relatedArticleSlug": f"{ds}/{slug}",
                })
        (out / "datasets" / "en").mkdir(parents=True, exist_ok=True)
        (out / "datasets" / "en" / f"{ds}.json").write_text(json.dumps(qs), encoding="utf-8")
    (out / "datasets" / "en" / "domains.json").write_text(json.dumps([
        {"slug": "A", "name": "A", "kind": "tool"},
    ]), encoding="utf-8")
    (out / "knowledge" / "en" / "index.json").write_text(json.dumps([
        {"slug": "A/art-1", "uuid": "A-u1", "domainSlug": "A",
         "title": "art-1", "path": "A/art-1.md", "order": 1, "description": "S"},
        {"slug": "A/art-2", "uuid": "A-u2", "domainSlug": "A",
         "title": "art-2", "path": "A/art-2.md", "order": 2, "description": "S"},
    ]), encoding="utf-8")
    return out


def test_selection_passes_when_satisfied(tree):
    from quizkb.release import check_pack
    status = check_pack(
        tree, "en", "A",
        selection={"articles": ["art-1"], "questions_per_article": 5},
    )
    assert status.ok is True
    assert status.blockers == []


def test_selection_blocks_on_missing_article(tree):
    from quizkb.release import check_pack
    status = check_pack(
        tree, "en", "A",
        selection={"articles": ["art-99"], "questions_per_article": 5},
    )
    assert status.ok is False
    assert any("art-99" in b and "missing" in b for b in status.blockers)


def test_selection_blocks_on_insufficient_questions(tree):
    from quizkb.release import check_pack
    status = check_pack(
        tree, "en", "A",
        selection={"articles": ["art-1"], "questions_per_article": 10},
    )
    assert status.ok is False
    assert any("art-1" in b and "5/10" in b for b in status.blockers)


def test_no_selection_uses_data_integrity_only(tree):
    from quizkb.release import check_pack
    status = check_pack(tree, "en", "A", selection=None)
    assert status.ok is True
