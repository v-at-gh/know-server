"""Admin-layer readiness: coverage badge aligns with check_pack.ok."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def minimal_tree(tmp_path, monkeypatch):
    """Build a minimal output/ tree for one domain in EN + RU with proper wiring."""
    out = tmp_path / "output"

    # EN syllabus: 1 article
    (out / "syllabus" / "en").mkdir(parents=True)
    (out / "syllabus" / "en" / "testdom.json").write_text(
        json.dumps([
            {"order": 1, "slug": "art1", "title": "Art 1",
             "summary": "S", "uuid": "syl-uuid-1"},
        ]),
        encoding="utf-8",
    )

    # EN article markdown (>=2000 bytes)
    (out / "knowledge" / "en" / "testdom").mkdir(parents=True)
    (out / "knowledge" / "en" / "testdom" / "art1.md").write_text(
        "# Art 1\n" + "x" * 2500, encoding="utf-8"
    )

    # EN index
    (out / "knowledge" / "en" / "index.json").write_text(
        json.dumps([{
            "slug": "testdom/art1", "uuid": "syl-uuid-1",
            "domainSlug": "testdom", "title": "Art 1",
            "description": "S", "path": "testdom/art1.md", "order": 1,
        }]),
        encoding="utf-8",
    )

    # EN domain meta
    (out / "datasets" / "en").mkdir(parents=True)
    (out / "datasets" / "en" / "domains.json").write_text(
        json.dumps([{"slug": "testdom", "name": "Test", "kind": "tool"}]),
        encoding="utf-8",
    )

    # EN dataset: 3 questions (below legacy 5 threshold)
    (out / "datasets" / "en" / "testdom.json").write_text(
        json.dumps([
            {"uuid": f"q-en-{i}", "text": f"Q{i}", "answer": f"A{i}",
             "relatedArticleSlug": "testdom/art1"}
            for i in range(3)
        ]),
        encoding="utf-8",
    )

    return out


def _setup_ru_complete(out: Path):
    """Add RU: article md, index, dataset with matching UUIDs, meta."""
    (out / "knowledge" / "ru" / "testdom").mkdir(parents=True, exist_ok=True)
    (out / "knowledge" / "ru" / "testdom" / "art1.md").write_text(
        "# Art 1\n" + "y" * 2500, encoding="utf-8"
    )
    (out / "knowledge" / "ru" / "index.json").write_text(
        json.dumps([{
            "slug": "testdom/art1", "uuid": "syl-uuid-1",
            "domainSlug": "testdom", "title": "Art 1 RU",
            "description": "S", "path": "testdom/art1.md", "order": 1,
        }]),
        encoding="utf-8",
    )
    (out / "datasets" / "ru").mkdir(parents=True, exist_ok=True)
    (out / "datasets" / "ru" / "domains.json").write_text(
        json.dumps([{"slug": "testdom", "name": "Test RU", "kind": "tool"}]),
        encoding="utf-8",
    )
    (out / "datasets" / "ru" / "testdom.json").write_text(
        json.dumps([
            {"uuid": f"q-en-{i}", "text": f"Q{i}", "answer": f"A{i}",
             "relatedArticleSlug": "testdom/art1"}
            for i in range(3)
        ]),
        encoding="utf-8",
    )


def test_coverage_badge_ok_when_complete(minimal_tree):
    _setup_ru_complete(minimal_tree)
    from quizkb.state import domain_summary
    from quizkb.release import check_pack

    summary = domain_summary(minimal_tree, "ru", "testdom")
    pack = check_pack(minimal_tree, "ru", "testdom")
    assert summary.status == "ok"
    assert pack.ok is True


def test_coverage_badge_fails_on_uuid_missing(minimal_tree):
    """3 EN questions, but RU has only 2 → UUID diff triggers partial."""
    _setup_ru_complete(minimal_tree)
    # Overwrite RU dataset with only 2 of 3 questions
    (minimal_tree / "datasets" / "ru" / "testdom.json").write_text(
        json.dumps([
            {"uuid": f"q-en-{i}", "text": f"Q{i}", "answer": f"A{i}",
             "relatedArticleSlug": "testdom/art1"}
            for i in range(2)
        ]),
        encoding="utf-8",
    )
    from quizkb.state import domain_summary
    from quizkb.release import check_pack

    summary = domain_summary(minimal_tree, "ru", "testdom")
    pack = check_pack(minimal_tree, "ru", "testdom")
    assert summary.status == "partial"
    assert pack.ok is False
    assert any("UUIDs missing" in b for b in pack.blockers)


def test_coverage_badge_fails_on_missing_file(minimal_tree):
    _setup_ru_complete(minimal_tree)
    (minimal_tree / "knowledge" / "ru" / "testdom" / "art1.md").unlink()
    from quizkb.state import domain_summary
    from quizkb.release import check_pack

    summary = domain_summary(minimal_tree, "ru", "testdom")
    pack = check_pack(minimal_tree, "ru", "testdom")
    assert summary.status == "partial"
    assert pack.ok is False
    assert any("missing file" in b for b in pack.blockers)


def test_coverage_badge_fails_on_index_mismatch(minimal_tree):
    _setup_ru_complete(minimal_tree)
    (minimal_tree / "knowledge" / "ru" / "index.json").write_text(
        "[]", encoding="utf-8"
    )
    from quizkb.state import domain_summary
    from quizkb.release import check_pack

    summary = domain_summary(minimal_tree, "ru", "testdom")
    pack = check_pack(minimal_tree, "ru", "testdom")
    assert summary.status == "partial"
    assert pack.ok is False


def test_coverage_badge_fails_on_missing_meta(minimal_tree):
    _setup_ru_complete(minimal_tree)
    (minimal_tree / "datasets" / "ru" / "domains.json").write_text(
        "[]", encoding="utf-8"
    )
    from quizkb.state import domain_summary
    from quizkb.release import check_pack

    summary = domain_summary(minimal_tree, "ru", "testdom")
    pack = check_pack(minimal_tree, "ru", "testdom")
    assert summary.status == "partial"
    assert pack.ok is False
    assert any("meta" in b for b in pack.blockers)


def test_coverage_status_matches_check_pack_ok(minimal_tree):
    """Critical alignment: same domain, same data → same verdict."""
    from quizkb.state import domain_summary
    from quizkb.release import check_pack

    # Complete case
    _setup_ru_complete(minimal_tree)
    summary = domain_summary(minimal_tree, "ru", "testdom")
    pack = check_pack(minimal_tree, "ru", "testdom")
    assert (summary.status == "ok") == pack.ok

    # Break UUID diff
    (minimal_tree / "datasets" / "ru" / "testdom.json").write_text(
        json.dumps([]), encoding="utf-8"
    )
    summary2 = domain_summary(minimal_tree, "ru", "testdom")
    pack2 = check_pack(minimal_tree, "ru", "testdom")
    assert (summary2.status == "ok") == pack2.ok
    assert summary2.status == "partial"
    assert pack2.ok is False


def test_check_pack_no_thin_warning(minimal_tree):
    """3 questions/article (below legacy 5) → NO warning emitted."""
    _setup_ru_complete(minimal_tree)
    from quizkb.release import check_pack

    pack = check_pack(minimal_tree, "ru", "testdom")
    assert pack.warnings == []
    assert pack.ok is True


def test_check_pack_en_no_uuid_check(minimal_tree):
    """EN is source lang — no UUID diff check, ready if files/index present."""
    from quizkb.release import check_pack
    pack = check_pack(minimal_tree, "en", "testdom")
    assert pack.ok is True
    assert pack.warnings == []
