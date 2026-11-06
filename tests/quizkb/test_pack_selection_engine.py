"""Boundary tests for check_pack with selection."""

import pytest
from tests.fixtures_pack_selection import make_output_tree


def test_check_pack_no_selection_baseline(tmp_path):
    root = make_output_tree(tmp_path)
    from quizkb.release import check_pack
    status = check_pack(root, "en", "alpha", selection=None)
    assert status.ok is True
    assert status.blockers == []


def test_check_pack_selection_complete(tmp_path):
    root = make_output_tree(tmp_path, articles=3, questions_per_article=5)
    from quizkb.release import check_pack
    status = check_pack(
        root, "en", "alpha",
        selection={"articles": ["art-1", "art-2"], "questions_per_article": 5},
    )
    assert status.ok is True
    assert status.blockers == []


def test_check_pack_selection_insufficient_questions(tmp_path):
    root = make_output_tree(tmp_path, articles=3, questions_per_article=5)
    from quizkb.release import check_pack
    status = check_pack(
        root, "en", "alpha",
        selection={"articles": ["art-1"], "questions_per_article": 10},
    )
    assert status.ok is False
    assert any("art-1" in b and "5/10" in b for b in status.blockers)


def test_check_pack_selection_missing_article(tmp_path):
    root = make_output_tree(tmp_path, articles=3, questions_per_article=5)
    from quizkb.release import check_pack
    status = check_pack(
        root, "en", "alpha",
        selection={"articles": ["art-99"], "questions_per_article": 5},
    )
    assert status.ok is False
    assert any("art-99" in b and "missing" in b for b in status.blockers)
