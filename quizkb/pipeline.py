from pathlib import Path

from .config import (
    OUTPUT_ROOT, SOURCE_LANG, TARGET_LANGS, DOMAINS,
    QUESTIONS_PER_ARTICLE,
)
from .io_utils import load_json


# --- Inventarization ---

def syllabus_for(lang: str, domain_slug: str) -> list[dict]:
    return load_json(OUTPUT_ROOT / "syllabus" / lang / f"{domain_slug}.json", []) or []


def questions_for(lang: str, domain_slug: str) -> list[dict]:
    return load_json(OUTPUT_ROOT / "datasets" / lang / f"{domain_slug}.json", []) or []


def knowledge_index(lang: str) -> list[dict]:
    return load_json(OUTPUT_ROOT / "knowledge" / lang / "index.json", []) or []


def article_path(lang: str, domain_slug: str, local_slug: str) -> Path:
    return OUTPUT_ROOT / "knowledge" / lang / domain_slug / f"{local_slug}.md"


def per_article_counts(qs: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for q in qs:
        rel = q.get("relatedArticleSlug", "")
        if "/" in rel:
            k = rel.split("/", 1)[1]
            counts[k] = counts.get(k, 0) + 1
    return counts


def domain_article_slugs(lang: str, domain_slug: str) -> set[str]:
    idx = knowledge_index(lang)
    return {e["slug"].split("/", 1)[1] for e in idx if e["domainSlug"] == domain_slug}


def diagnose(lang: str, domain_slug: str, expected_articles: set[str]) -> dict:
    """Единая диагностика по одной паре (lang, domain)."""
    idx_slugs = domain_article_slugs(lang, domain_slug)
    missing_articles = sorted(expected_articles - idx_slugs)
    files_missing = sorted(
        s for s in idx_slugs if not article_path(lang, domain_slug, s).exists()
    )

    qs = questions_for(lang, domain_slug)
    counts = per_article_counts(qs)
    zero  = sorted(s for s in expected_articles if counts.get(s, 0) == 0)
    thin  = sorted(s for s in expected_articles
                   if 0 < counts.get(s, 0) < QUESTIONS_PER_ARTICLE)

    uuid_missing: list[str] = []
    if lang != SOURCE_LANG:
        src = questions_for(SOURCE_LANG, domain_slug)
        src_uuids = {q["uuid"] for q in src}
        dst_uuids = {q["uuid"] for q in qs}
        uuid_missing = sorted(src_uuids - dst_uuids)

    return {
        "lang": lang,
        "domain": domain_slug,
        "questions_total": len(qs),
        "articles_missing": missing_articles,
        "files_missing": files_missing,
        "zero_articles": zero,
        "thin_articles": thin,
        "uuid_missing": uuid_missing,
    }

def difficulty_for_order(order: int, total: int) -> int:
    third = max(1, total // 3)
    if order <= third:
        return 1
    if order <= 2 * third:
        return 2
    return 3
