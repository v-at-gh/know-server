"""_copy_pack filtering tests: selection subsets correctly filtered."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def tree(tmp_path):
    """Build minimal output tree with 2 articles per domain."""
    out = tmp_path / "output"
    for ds in ("A", "B"):
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
        (out / "datasets" / "en").mkdir(parents=True, exist_ok=True)
        qs = []
        for slug in ("art-1", "art-2"):
            for i in range(5):
                qs.append({
                    "uuid": f"{ds}-{slug}-q{i}",
                    "text": f"Q{i}", "answer": f"A{i}",
                    "relatedArticleSlug": f"{ds}/{slug}",
                })
        (out / "datasets" / "en" / f"{ds}.json").write_text(json.dumps(qs), encoding="utf-8")
    (out / "datasets" / "en" / "domains.json").write_text(json.dumps([
        {"slug": "A", "name": "A", "kind": "tool"},
        {"slug": "B", "name": "B", "kind": "tool"},
    ]), encoding="utf-8")
    (out / "knowledge" / "en" / "index.json").write_text(json.dumps([
        {"slug": f"{ds}/{slug}", "uuid": f"{ds}-{slug}",
         "domainSlug": ds, "title": slug, "path": f"{ds}/{slug}.md",
         "order": i+1, "description": "S"}
        for ds in ("A", "B")
        for i, slug in enumerate(("art-1", "art-2"))
    ]), encoding="utf-8")
    return out


def test_copy_pack_filters_to_selection(tree, tmp_path):
    from quizkb.release import _copy_pack
    release = tmp_path / "release"
    release.mkdir()

    _copy_pack(
        tree, release, "en", "A",
        selection={"articles": ["art-1"], "questions_per_article": 5},
    )

    ds = json.loads((release / "datasets" / "en" / "A.json").read_text())
    assert len(ds) == 5
    assert all(q["relatedArticleSlug"] == "A/art-1" for q in ds)

    idx = json.loads((release / "knowledge" / "en" / "index.json").read_text())
    a_entries = [e for e in idx if e["domainSlug"] == "A"]
    assert len(a_entries) == 1
    assert a_entries[0]["slug"] == "A/art-1"

    assert (release / "knowledge" / "en" / "A" / "art-1.md").exists()
    assert not (release / "knowledge" / "en" / "A" / "art-2.md").exists()


def test_copy_pack_without_selection_copies_all(tree, tmp_path):
    from quizkb.release import _copy_pack
    release = tmp_path / "release"
    release.mkdir()

    _copy_pack(tree, release, "en", "A", selection=None)

    ds = json.loads((release / "datasets" / "en" / "A.json").read_text())
    assert len(ds) == 10
    idx = json.loads((release / "knowledge" / "en" / "index.json").read_text())
    a_entries = [e for e in idx if e["domainSlug"] == "A"]
    assert len(a_entries) == 2
    assert (release / "knowledge" / "en" / "A" / "art-2.md").exists()


def test_copy_pack_multiple_selections_across_domains(tree, tmp_path):
    from quizkb.release import _copy_pack
    release = tmp_path / "release"
    release.mkdir()

    _copy_pack(
        tree, release, "en", "A",
        selection={"articles": ["art-1"], "questions_per_article": 5},
    )
    _copy_pack(
        tree, release, "en", "B",
        selection={"articles": ["art-2"], "questions_per_article": 5},
    )

    assert (release / "knowledge" / "en" / "A" / "art-1.md").exists()
    assert not (release / "knowledge" / "en" / "A" / "art-2.md").exists()
    assert (release / "knowledge" / "en" / "B" / "art-2.md").exists()
    assert not (release / "knowledge" / "en" / "B" / "art-1.md").exists()
