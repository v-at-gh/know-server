"""E2E tests for the pack release chain.

Documents current behavior of check_pack / build_release / build_qap / validate_qap
including the known hardcoded-5 bug (User State v1 will change this deliberately).
"""

from __future__ import annotations
import json
import shutil
from pathlib import Path

import pytest

from tests.fixtures_pack_e2e import (
    build_minimal_output_tree,
)


def test_happy_path_builds_and_validates(tmp_path):
    out = build_minimal_output_tree(tmp_path)
    staging_dir = tmp_path / "staging" / "test_pack"

    from quizkb import release
    from scripts import build_qap, validate_qap

    report = release.build_release(
        output_root=out,
        release_root=staging_dir,
        languages=["en"],
        domains=[{"slug": "testdom", "name": "Testdom", "kind": "topic"}],
        campaigns=[],
        verbose=False,
    )
    assert report.ready_packs == 1

    qap_path = tmp_path / "test.qap"
    build_qap.build(
        staging_dir,
        qap_path,
        pack_id="test_pack",
        pack_version="2026.10.28",
        kind="campaign",
    )

    assert qap_path.exists()
    assert qap_path.stat().st_size > 1000

    problems = validate_qap.validate(qap_path)
    assert problems == []


def test_check_pack_accepts_5_per_article(tmp_path):
    """Lock current behavior: 5 == QUESTIONS_PER_ARTICLE, ok."""
    out = build_minimal_output_tree(tmp_path, questions_per_article=5)
    from quizkb import release
    status = release.check_pack(output_root=out, lang="en", domain_slug="testdom")
    assert status.ok is True
    assert status.blockers == []


def test_check_pack_no_thin_warning_regardless_of_quantity(tmp_path):
    """Post-C.2.5: warnings removed entirely. Any quantity passes if
    data-integrity checks pass. Quantity is user-scoped concern.
    """
    out = build_minimal_output_tree(tmp_path, questions_per_article=3)
    from quizkb.release import check_pack
    status = check_pack(output_root=out, lang="en", domain_slug="testdom")
    assert status.ok is True
    assert status.warnings == []


def test_check_pack_rejects_missing_article_md(tmp_path):
    out = build_minimal_output_tree(tmp_path, include_article_md=False)
    from quizkb import release
    status = release.check_pack(output_root=out, lang="en", domain_slug="testdom")
    assert status.ok is False
    assert any("md" in b.lower() or "missing" in b.lower() for b in status.blockers)


def test_check_pack_rejects_missing_domain_meta(tmp_path):
    out = build_minimal_output_tree(tmp_path, include_domain_meta=False)
    from quizkb import release
    status = release.check_pack(output_root=out, lang="en", domain_slug="testdom")
    assert status.ok is False
    assert len(status.blockers) > 0


def test_install_qap_merges_preserving_unrelated(tmp_path):
    """Build a .qap, install into a target dir with pre-existing unrelated
    files, verify unrelated survives and QAP content lands."""
    out = build_minimal_output_tree(tmp_path)
    staging_dir = tmp_path / "staging" / "test_pack"

    from quizkb import release
    from scripts import build_qap, install_qap

    release.build_release(
        output_root=out,
        release_root=staging_dir,
        languages=["en"],
        domains=[{"slug": "testdom", "name": "Testdom", "kind": "topic"}],
        campaigns=[],
        verbose=False,
    )
    qap = tmp_path / "test.qap"
    build_qap.build(
        staging_dir,
        qap,
        pack_id="test_pack",
        pack_version="2026.10.28",
        kind="campaign",
    )

    target = tmp_path / "android_assets"
    target.mkdir()
    (target / "unrelated.txt").write_text("keep me", encoding="utf-8")

    install_qap.install(qap, target)

    assert (target / "unrelated.txt").read_text(encoding="utf-8") == "keep me"
    assert (target / "qap" / "manifest.json").exists()
    assert (target / "datasets" / "en" / "domains.json").exists()
