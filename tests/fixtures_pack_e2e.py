"""Minimal pack-release fixture tree for E2E tests."""

from __future__ import annotations
import json
from pathlib import Path

# 2001 bytes of markdown — just over MIN_MD_BYTES (2000) from release.py
_ARTICLE_BODY = "# Test Article\n\n" + ("Lorem ipsum dolor sit amet. " * 100)[:2001]


def build_minimal_output_tree(
    root: Path,
    *,
    domain: str = "testdom",
    questions_per_article: int = 5,
    articles: int = 1,
    include_domain_meta: bool = True,
    include_article_md: bool = True,
) -> Path:
    """Constructs a minimal `output/` tree that check_pack considers ready
    (with defaults). Returns the output root.
    """
    out = root / "output"
    (out / "syllabus" / "en").mkdir(parents=True, exist_ok=True)
    (out / "datasets" / "en").mkdir(parents=True, exist_ok=True)
    (out / "knowledge" / "en" / domain).mkdir(parents=True, exist_ok=True)

    syllabus = [
        {
            "slug": f"topic-{i}",
            "uuid": f"syl-uuid-{i}",
            "title": f"Topic {i}",
            "summary": f"Summary of topic {i}",
            "order": i + 1,
        }
        for i in range(articles)
    ]
    _write_json(out / "syllabus" / "en" / f"{domain}.json", syllabus)

    if include_article_md:
        for item in syllabus:
            (out / "knowledge" / "en" / domain / f"{item['slug']}.md").write_text(
                _ARTICLE_BODY, encoding="utf-8"
            )

    index = [
        {
            "slug": f"{domain}/{item['slug']}",
            "uuid": item["uuid"],
            "domainSlug": domain,
            "title": item["title"],
            "description": item["summary"],
            "path": f"{domain}/{item['slug']}.md",
            "order": item["order"],
        }
        for item in syllabus
    ]
    _write_json(out / "knowledge" / "en" / "index.json", index)

    questions = []
    for item in syllabus:
        for q_idx in range(questions_per_article):
            questions.append({
                "uuid": f"q-uuid-{item['slug']}-{q_idx}",
                "text": f"Question {q_idx} about {item['title']}?",
                "answer": f"Answer {q_idx}",
                "distractors": [
                    {"text": "wrong1"},
                    {"text": "wrong2"},
                    {"text": "wrong3"},
                    {"text": "wrong4"},
                ],
                "hints": ["hint1", "hint2", "hint3"],
                "difficulty": 1,
                "type": "select",
                "relatedArticleSlug": f"{domain}/{item['slug']}",
            })
    _write_json(out / "datasets" / "en" / f"{domain}.json", questions)

    domains_meta = []
    if include_domain_meta:
        domains_meta.append({
            "slug": domain,
            "name": domain.title(),
            "description": f"Test domain {domain}",
            "kind": "topic",
        })
    _write_json(out / "datasets" / "en" / "domains.json", domains_meta)

    _write_json(out / "datasets" / "en" / "campaigns.json", [])

    return out


def build_minimal_packs_json(root: Path, domain: str = "testdom") -> Path:
    """Writes a packs.json that references the minimal output tree."""
    packs_path = root / "state" / "packs.json"
    packs_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(packs_path, {
        "packs": [
            {
                "id": "test_pack",
                "kind": "campaign",
                "domains": [domain],
                "campaigns": [],
                "languages": ["en"],
            }
        ]
    })
    return packs_path


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
