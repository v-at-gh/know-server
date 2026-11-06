"""Read-only views over the pipeline output tree.

Every function here answers a question about what exists on disk.
None of them writes anything. All are safe to call from a web request.
"""
from __future__ import annotations

import json

from typing import Dict, Optional
from dataclasses import dataclass
from pathlib import Path

from .config import (
    SOURCE_LANG, TARGET_LANGS, DOMAINS, CAMPAIGNS,
    QUESTIONS_PER_ARTICLE
)
from .io_utils import load_json, load_json_cached
from .parsing import DISTRACTOR_WHY_FORBIDDEN

MIN_MD_BYTES = 2000


@dataclass
class ActualContent:
    articles: int
    short_articles: int
    questions: int
    missing_uuids: list[str]
    has_syllabus: bool
    has_dataset: bool


@dataclass
class DomainSummary:
    lang: str
    slug: str
    kind: str
    articles: int
    questions: int
    expected_articles: int
    short_articles: int
    status: str = "ok"


def _readiness_blockers(
    output_root: Path,
    lang: str,
    domain_slug: str,
    cache: dict | None = None,
) -> list[str]:
    """Admin-layer readiness check: data facts only, no quantity heuristics.

    Checks (mirrors check_pack blockers pre-refactor):
      - EN syllabus exists and articles index matches
      - Domain meta present in datasets/{lang}/domains.json
      - All expected markdown files present and non-empty (>= MIN_MD_BYTES)
      - For non-source langs: all source UUIDs present in target dataset

    Returns blocker strings. Empty list = admin-ready.
    Quantity of questions per article is NOT checked here — that is a
    user-scoped concern resolved at pack-selection layer.
    """
    blockers: list[str] = []

    syllabus_en = load_json_cached(
        output_root / "syllabus" / SOURCE_LANG / f"{domain_slug}.json",
        [],
        cache=cache,
    ) or []
    expected = {it["slug"] for it in syllabus_en}
    if not expected:
        blockers.append(f"no syllabus for {domain_slug}")
        return blockers

    idx_all = load_json_cached(
        output_root / "knowledge" / lang / "index.json", [], cache=cache
    ) or []
    idx_slugs = {
        e["slug"].split("/", 1)[1]
        for e in idx_all
        if e.get("domainSlug") == domain_slug
    }
    if idx_slugs != expected:
        blockers.append(
            f"index articles mismatch: {len(idx_slugs)} vs {len(expected)}"
        )

    domains_meta = load_json_cached(
        output_root / "datasets" / lang / "domains.json", [], cache=cache
    ) or []
    if not any(d.get("slug") == domain_slug for d in domains_meta):
        blockers.append(f"no domain meta in datasets/{lang}/domains.json")

    for s in idx_slugs:
        md = output_root / "knowledge" / lang / domain_slug / f"{s}.md"
        if not md.exists():
            blockers.append(f"missing file {s}.md")
            break
        if md.stat().st_size < MIN_MD_BYTES:
            blockers.append(f"empty file {s}.md ({md.stat().st_size} bytes)")
            break

    if lang != SOURCE_LANG:
        src_uuids = {
            q["uuid"]
            for q in (
                load_json_cached(
                    output_root / "datasets" / SOURCE_LANG / f"{domain_slug}.json",
                    [],
                    cache=cache,
                )
                or []
            )
        }
        dst_uuids = {
            q["uuid"]
            for q in (
                load_json_cached(
                    output_root / "datasets" / lang / f"{domain_slug}.json",
                    [],
                    cache=cache,
                )
                or []
            )
        }
        lost = src_uuids - dst_uuids
        if lost:
            blockers.append(f"{len(lost)} UUIDs missing in translation")

    return blockers


def domain_summary(output_root: Path, lang: str, slug: str, cache: Optional[Dict] = None) -> DomainSummary:
    knowledge_dir = output_root / "knowledge" / lang / slug
    dataset_path = output_root / "datasets" / lang / f"{slug}.json"
    syllabus_path = output_root / "syllabus" / SOURCE_LANG / f"{slug}.json"

    articles = 0
    short_articles = 0
    if knowledge_dir.is_dir():
        for md in knowledge_dir.glob("*.md"):
            articles += 1
            if md.stat().st_size < MIN_MD_BYTES:
                short_articles += 1

    questions = len([q for q in load_json_cached(dataset_path, [], cache=cache) or [] if not q.get("archived")])

    syllabus = load_json_cached(syllabus_path, [], cache=cache) or []
    expected_articles = len(syllabus)

    from .config import DOMAINS
    domain_conf = next((d for d in DOMAINS if d["slug"] == slug), None)
    kind = domain_conf.get("kind", "unknown") if domain_conf else "unknown"

    if expected_articles == 0:
        status = "absent" if (articles == 0 and questions == 0) else "unknown"
    elif articles == 0 and questions == 0:
        status = "absent"
    else:
        blockers = _readiness_blockers(output_root, lang, slug, cache=cache)
        status = "ok" if not blockers else "partial"

    return DomainSummary(
        lang=lang,
        slug=slug,
        kind=kind,
        articles=articles,
        questions=questions,
        expected_articles=expected_articles,
        short_articles=short_articles,
        status=status,
    )


def coverage_rows(
    output_root: Path,
    langs: list[str],
    domains: list[dict],
    cache: Optional[Dict] = None,
) -> list[dict]:
    """One row per language. Each row carries a list of DomainSummary
    aligned with `domains`."""
    rows: list[dict] = []
    for lang in langs:
        cells = [domain_summary(output_root, lang, d["slug"], cache=cache) for d in domains]
        rows.append({"lang": lang, "cells": cells})
    return rows


def actual_content(
    output_root: Path, slug: str, lang: str, cache: Optional[Dict] = None
) -> ActualContent:
    knowledge_dir = output_root / "knowledge" / lang / slug
    dataset_path = output_root / "datasets" / lang / f"{slug}.json"
    syllabus_path = output_root / "syllabus" / SOURCE_LANG / f"{slug}.json"

    has_syllabus = syllabus_path.exists()
    has_dataset = dataset_path.exists()

    articles = 0
    short_articles = 0
    if knowledge_dir.is_dir():
        for md in knowledge_dir.glob("*.md"):
            articles += 1
            try:
                if md.stat().st_size < MIN_MD_BYTES:
                    short_articles += 1
            except Exception:
                pass

    dataset = load_json_cached(dataset_path, [], cache=cache) or []
    questions = len([q for q in dataset if not q.get("archived")])

    missing_uuids: list[str] = []
    if lang != SOURCE_LANG and has_syllabus and has_dataset:
        from .translation import audit_missing_questions
        missing_uuids = audit_missing_questions(output_root, lang=lang, domain_slug=slug, cache=cache)

    return ActualContent(
        articles=articles,
        short_articles=short_articles,
        questions=questions,
        missing_uuids=missing_uuids,
        has_syllabus=has_syllabus,
        has_dataset=has_dataset,
    )


def expected_articles(
    output_root: Path, slug: str, cache: Optional[Dict] = None
) -> int:
    syllabus_path = output_root / "syllabus" / SOURCE_LANG / f"{slug}.json"
    syllabus = load_json_cached(syllabus_path, [], cache=cache) or []
    return len(syllabus)


def expected_questions(
    articles: int, questions_types: dict | None = None
) -> int:
    if questions_types:
        return articles * sum(questions_types.values())
    return articles * QUESTIONS_PER_ARTICLE


def evaluate_domain_status(
    output_root: Path,
    slug: str,
    lang: str,
    questions_types: dict | None = None,
    cache: Optional[Dict] = None,
) -> dict:
    syllabus_en_path = output_root / "syllabus" / SOURCE_LANG / f"{slug}.json"
    dataset_en_path = output_root / "datasets" / SOURCE_LANG / f"{slug}.json"

    if not syllabus_en_path.exists() or not dataset_en_path.exists():
        return {
            "state": "absent_source",
            "articles": 0,
            "expected_articles": 0,
            "questions": 0,
            "expected_questions": 0,
        }

    actual = actual_content(output_root, slug, lang, cache=cache)
    exp_art = expected_articles(output_root, slug, cache=cache)
    if exp_art == 0:
        return {
            "state": "absent_source",
            "articles": actual.articles,
            "expected_articles": exp_art,
            "questions": actual.questions,
            "expected_questions": 0,
        }

    exp_q = expected_questions(exp_art, questions_types)
    articles = actual.articles
    short_articles = actual.short_articles
    questions = actual.questions

    if not actual.has_syllabus and not actual.has_dataset:
        return {
            "state": "missing",
            "articles": articles,
            "expected_articles": exp_art,
            "questions": questions,
            "expected_questions": exp_q,
            "missing_uuids": exp_q if lang != SOURCE_LANG else 0,
        }

    missing_uuids_count = len(actual.missing_uuids)

    is_complete = (
        articles == exp_art
        and short_articles == 0
        and questions >= exp_q
        and (lang == SOURCE_LANG or missing_uuids_count == 0)
    )

    if is_complete:
        state = "bootstrapped"
    elif articles == 0 and questions == 0:
        state = "missing"
    else:
        state = "partial"

    res = {
        "state": state,
        "articles": articles,
        "expected_articles": exp_art,
        "questions": questions,
        "expected_questions": exp_q,
    }
    if lang != SOURCE_LANG:
        res["missing_uuids"] = missing_uuids_count
    return res


@dataclass
class ErrorRecord:
    ts: str
    run_id: str
    cell: str | None
    label: str
    reason: str
    prompt: str
    raw_response: str
    detail: str = ""

    @property
    def prompt_len(self) -> int:
        return len(self.prompt)

    @property
    def has_response(self) -> bool:
        return bool(self.raw_response)

    @property
    def has_prompt(self) -> bool:
        return bool(self.prompt)


def recent_errors(
    logs_root: Path,
    *,
    limit_runs: int = 20,
) -> list[ErrorRecord]:
    """Load error records from the N most recent run directories.

    Sorted by ts descending. Reads errors.jsonl, skips malformed lines.
    """
    if not logs_root.is_dir():
        return []

    run_dirs = sorted(
        (p for p in logs_root.iterdir()
         if p.is_dir() and p.name != "archive"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit_runs]

    records: list[ErrorRecord] = []
    for run_dir in run_dirs:
        path = run_dir / "errors.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw = obj.get("raw_response", "") or ""
            records.append(ErrorRecord(
                ts=obj.get("ts", ""),
                run_id=run_dir.name,
                cell=obj.get("cell"),
                label=obj.get("label", ""),
                reason=obj.get("reason", ""),
                raw_response=raw,
                prompt=obj.get("prompt", "") or "",
                detail=obj.get("detail", "") or "",
            ))

    records.sort(key=lambda r: r.ts, reverse=True)
    return records


def error_summary(records: list[ErrorRecord]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for r in records:
        counts[r.reason] = counts.get(r.reason, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def label_lang(label: str) -> str | None:
    """Extract a language code from a label.

    Covers all label kinds produced by the pipeline:
      syl:<lang>:<domain>
      art:<lang>:<domain>[/<slug>]
      q:<lang>:<uuid_prefix>#<n>
      campaign:<lang>:<slug>
      dommeta:<lang>:<slug>
      fix:<lang>[#<n>]
      1q:<domain>/<slug>#<n>       (always SOURCE_LANG)
    """
    if label.startswith("1q:"):
        return SOURCE_LANG
    parts = label.split(":")
    if len(parts) < 2:
        return None
    candidate = parts[1].split("#", 1)[0]
    if len(candidate) in (2, 3):
        return candidate
    return None


def error_label_languages(records: list[ErrorRecord]) -> list[tuple[str, int]]:
    """Rough language histogram based on label structure.

    Labels look like `q:ru:abc12345#1`, `art:ru:domain/slug`, `syl:ru:domain`.
    The second colon-separated segment is the language for most labels.
    """
    counts: dict[str, int] = {}
    for r in records:
        lang = label_lang(r.label)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


@dataclass
class ArticleRow:
    order: int
    slug: str
    title: str
    md_bytes: int
    questions: int
    short: bool

    @property
    def missing(self) -> bool:
        return self.md_bytes == 0


@dataclass
class DomainDetail:
    lang: str
    slug: str
    display_name: str
    display_description: str
    campaigns: list[str]
    articles: list[ArticleRow]
    expected_articles: int
    questions_total: int
    uuid_missing: list[str]
    status: str
    has_meta: bool = True

    @property
    def short_articles_count(self) -> int:
        return sum(1 for a in self.articles if a.short)

    @property
    def missing_articles_count(self) -> int:
        return sum(1 for a in self.articles if a.missing)


def domain_detail(
    output_root: Path,
    lang: str,
    slug: str,
    cache: Optional[Dict] = None,
) -> DomainDetail | None:
    """Everything the drill-down page needs for one (lang, domain) pair.
    Returns None if the domain has no syllabus at all (unknown slug).
    """
    syllabus = load_json_cached(
        output_root / "syllabus" / SOURCE_LANG / f"{slug}.json", [], cache=cache
    ) or []
    if not syllabus:
        syllabus = load_json_cached(
            output_root / "syllabus" / lang / f"{slug}.json", [], cache=cache
        ) or []
    if not syllabus:
        return None

    domains_meta = load_json_cached(
        output_root / "datasets" / lang / "domains.json", [], cache=cache
    ) or []

    meta_entry = next((d for d in domains_meta if d.get("slug") == slug), None)
    display_name = meta_entry["name"] if meta_entry else slug
    display_description = meta_entry.get("description", "") if meta_entry else ""
    has_meta = meta_entry is not None

    campaigns_meta = load_json_cached(
        output_root / "datasets" / lang / "campaigns.json", [], cache=cache
    ) or []
    campaigns = [
        c["slug"] for c in campaigns_meta
        if slug in c.get("domainSlugs", [])
    ]

    dataset = load_json_cached(
        output_root / "datasets" / lang / f"{slug}.json", [], cache=cache
    ) or []
    by_article: dict[str, int] = {}
    for q in dataset:
        if q.get("archived"):
            continue
        rel = q.get("relatedArticleSlug", "")
        if "/" in rel:
            local = rel.split("/", 1)[1]
            by_article[local] = by_article.get(local, 0) + 1

    articles: list[ArticleRow] = []
    for item in syllabus:
        md = output_root / "knowledge" / lang / slug / f"{item['slug']}.md"
        size = md.stat().st_size if md.exists() else 0
        articles.append(ArticleRow(
            order=item["order"],
            slug=item["slug"],
            title=item["title"],
            md_bytes=size,
            questions=by_article.get(item["slug"], 0),
            short=(0 < size < MIN_MD_BYTES),
        ))

    uuid_missing: list[str] = []
    if lang != SOURCE_LANG:
        src = load_json_cached(
            output_root / "datasets" / SOURCE_LANG / f"{slug}.json", [], cache=cache
        ) or []
        src_uuids = {q["uuid"] for q in src}
        dst_uuids = {q["uuid"] for q in dataset}
        uuid_missing = sorted(src_uuids - dst_uuids)

    summary = domain_summary(output_root, lang, slug, cache=cache)
    return DomainDetail(
        lang=lang,
        slug=slug,
        display_name=display_name,
        display_description=display_description,
        campaigns=campaigns,
        articles=articles,
        expected_articles=len(syllabus),
        questions_total=len(dataset),
        uuid_missing=uuid_missing,
        status=summary.status,
        has_meta=has_meta,
    )


@dataclass
class ArticleContent:
    lang: str
    domain: str
    slug: str
    title: str
    summary: str
    md_bytes: int
    text: str
    exists: bool
    short: bool


@dataclass
class QuestionView:
    uuid: str
    text: str
    answer: str
    distractors: list[dict]
    hints: list[str]
    difficulty: int
    present: bool
    archived: bool = False


@dataclass
class QuestionPair:
    uuid: str
    difficulty: int
    source: QuestionView   # in the compare language (usually EN)
    target: QuestionView   # in the current language
    distractor_delta: int
    hint_delta: int


def syllabus_item(output_root: Path, lang: str, domain: str, slug: str, cache: Optional[Dict] = None) -> dict | None:
    for l in (lang, SOURCE_LANG):
        syllabus = load_json_cached(output_root / "syllabus" / l / f"{domain}.json", [], cache=cache) or []
        item = next((it for it in syllabus if it["slug"] == slug), None)
        if item is not None:
            return item
    return None


_syllabus_item = syllabus_item


def article_content(
    output_root: Path,
    lang: str,
    domain: str,
    slug: str,
    cache: Optional[Dict] = None,
) -> ArticleContent:
    item = syllabus_item(output_root, lang, domain, slug, cache=cache)
    title = item["title"] if item else slug
    summary = item["summary"] if item else ""

    md = output_root / "knowledge" / lang / domain / f"{slug}.md"
    if md.exists():
        text = md.read_text(encoding="utf-8")
        size = md.stat().st_size
    else:
        text = ""
        size = 0

    return ArticleContent(
        lang=lang, domain=domain, slug=slug,
        title=title, summary=summary,
        md_bytes=size, text=text,
        exists=md.exists(),
        short=(0 < size < MIN_MD_BYTES),
    )


def questions_for_article(
    output_root: Path,
    lang: str,
    domain: str,
    slug: str,
    cache: Optional[Dict] = None,
) -> list[QuestionView]:
    dataset = load_json_cached(output_root / "datasets" / lang / f"{domain}.json", [], cache=cache) or []
    article_key = f"{domain}/{slug}"
    out: list[QuestionView] = []
    for q in dataset:
        if q.get("archived"):
            continue
        if q.get("relatedArticleSlug") != article_key:
            continue
        out.append(QuestionView(
            uuid=q["uuid"],
            text=q.get("text", ""),
            answer=q.get("answer", ""),
            distractors=list(q.get("distractors", [])),
            hints=list(q.get("hints", [])),
            difficulty=q.get("difficulty", 1),
            present=True,
            archived=q.get("archived", False),
        ))
    return out


def _empty_view(uuid: str, difficulty: int) -> QuestionView:
    return QuestionView(
        uuid=uuid, text="", answer="", distractors=[], hints=[],
        difficulty=difficulty, present=False,
    )


def question_pairs(
    output_root: Path,
    lang_a: str,
    lang_b: str,
    domain: str,
    slug: str,
    cache: Optional[Dict] = None,
) -> list[QuestionPair]:
    """Pair questions by UUID between two languages. `lang_a` is treated
    as the source, `lang_b` as the target."""
    a_list = questions_for_article(output_root, lang_a, domain, slug, cache=cache)
    b_list = questions_for_article(output_root, lang_b, domain, slug, cache=cache)
    b_by_uuid = {q.uuid: q for q in b_list}

    pairs: list[QuestionPair] = []
    seen: set[str] = set()

    for qa in a_list:
        seen.add(qa.uuid)
        qb = b_by_uuid.get(qa.uuid) or _empty_view(qa.uuid, qa.difficulty)
        pairs.append(QuestionPair(
            uuid=qa.uuid,
            difficulty=qa.difficulty,
            source=qa,
            target=qb,
            distractor_delta=len(qb.distractors) - len(qa.distractors),
            hint_delta=len(qb.hints) - len(qa.hints),
        ))

    for qb in b_list:
        if qb.uuid in seen:
            continue
        pairs.append(QuestionPair(
            uuid=qb.uuid,
            difficulty=qb.difficulty,
            source=_empty_view(qb.uuid, qb.difficulty),
            target=qb,
            distractor_delta=len(qb.distractors),
            hint_delta=len(qb.hints),
        ))

    pairs.sort(key=lambda p: (p.difficulty, p.uuid))
    return pairs


@dataclass
class QuestionAcrossLang:
    lang: str
    present: bool
    text: str
    answer: str
    distractors: list[dict]
    hints: list[str]
    difficulty: int


@dataclass
class QuestionAcrossView:
    uuid: str
    domain_slug: str | None
    article_slug: str | None
    languages: list[QuestionAcrossLang]
    archived: bool = False


def find_question_locations(
    output_root: Path,
    uuid: str,
    cache: Optional[Dict] = None,
) -> list[tuple[str, str, dict]]:
    """Return (lang, domain, question_dict) for every dataset containing this UUID."""
    results: list[tuple[str, str, dict]] = []
    for lang in [SOURCE_LANG] + list(TARGET_LANGS):
        ddir = output_root / "datasets" / lang
        if not ddir.is_dir():
            continue
        for path in sorted(ddir.glob("*.json")):
            if path.name in ("domains.json", "campaigns.json"):
                continue
            dataset = load_json_cached(path, [], cache=cache) or []
            for q in dataset:
                if q.get("uuid") == uuid:
                    results.append((lang, path.stem, q))
                    break
    return results


def question_across_languages(
    output_root: Path,
    uuid: str,
    cache: Optional[Dict] = None,
) -> QuestionAcrossView:
    locations = find_question_locations(output_root, uuid, cache=cache)
    if not locations:
        return QuestionAcrossView(uuid=uuid, domain_slug=None,
                                  article_slug=None, languages=[])

    en_loc = next((loc for loc in locations if loc[0] == SOURCE_LANG), None)
    meta_loc = en_loc or locations[0]
    domain_slug = meta_loc[1]
    article_slug = meta_loc[2].get("relatedArticleSlug")

    by_lang = {loc[0]: loc[2] for loc in locations}

    entries: list[QuestionAcrossLang] = []
    for lang in [SOURCE_LANG] + list(TARGET_LANGS):
        q = by_lang.get(lang)
        if q is None:
            entries.append(QuestionAcrossLang(
                lang=lang, present=False, text="", answer="",
                distractors=[], hints=[], difficulty=0,
            ))
        else:
            entries.append(QuestionAcrossLang(
                lang=lang, present=True,
                text=q.get("text", ""),
                answer=q.get("answer", ""),
                distractors=list(q.get("distractors", [])),
                hints=list(q.get("hints", [])),
                difficulty=q.get("difficulty", 1),
            ))

    archived = any(q.get("archived") for _, _, q in locations)
    return QuestionAcrossView(
        uuid=uuid, domain_slug=domain_slug,
        article_slug=article_slug, languages=entries,
        archived=archived,
    )


@dataclass
class QuestionEntry:
    uuid: str
    text: str
    type: str
    difficulty: int
    archived: bool
    state: str
    related_article_slug: str


def domain_questions(
    output_root: Path,
    lang: str,
    slug: str,
    cache: Optional[Dict] = None,
) -> list[QuestionEntry]:
    """Return all questions for a domain in a target language with readiness state."""
    src_dataset = load_json_cached(output_root / "datasets" / SOURCE_LANG / f"{slug}.json", [], cache=cache) or []
    dst_dataset = load_json_cached(output_root / "datasets" / lang / f"{slug}.json", [], cache=cache) or []
    dst_by_uuid = {q["uuid"]: q for q in dst_dataset if "uuid" in q}

    out: list[QuestionEntry] = []
    seen_uuids: set[str] = set()

    for en_q in src_dataset:
        uuid = en_q.get("uuid", "")
        if not uuid:
            continue
        seen_uuids.add(uuid)
        target_q = dst_by_uuid.get(uuid)

        is_archived = bool(en_q.get("archived") or (target_q and target_q.get("archived")))
        rel = en_q.get("relatedArticleSlug", "") or (target_q.get("relatedArticleSlug", "") if target_q else "")
        art_slug = rel.split("/", 1)[1] if "/" in rel else rel

        if is_archived:
            text = target_q.get("text", "") if target_q else en_q.get("text", "")
            diff = target_q.get("difficulty", 1) if target_q else en_q.get("difficulty", 1)
            out.append(QuestionEntry(
                uuid=uuid,
                text=text,
                type="select",
                difficulty=diff,
                archived=True,
                state="archived",
                related_article_slug=art_slug,
            ))
            continue

        if target_q is None and lang != SOURCE_LANG:
            out.append(QuestionEntry(
                uuid=uuid,
                text=en_q.get("text", ""),
                type="select",
                difficulty=en_q.get("difficulty", 1),
                archived=False,
                state="missing",
                related_article_slug=art_slug,
            ))
            continue

        q_curr = target_q if target_q is not None else en_q
        text = q_curr.get("text", "")
        answer = q_curr.get("answer", "")
        distractors = q_curr.get("distractors", [])
        hints = q_curr.get("hints", [])
        difficulty = q_curr.get("difficulty", 1)

        is_broken = False
        if not text.strip() or not answer.strip():
            is_broken = True
        elif lang != SOURCE_LANG:
            en_distractors = en_q.get("distractors", [])
            en_hints = en_q.get("hints", [])
            if len(distractors) != len(en_distractors):
                is_broken = True
            elif len(hints) != len(en_hints):
                is_broken = True
            elif answer.strip() and answer.strip() == en_q.get("answer", "").strip():
                is_broken = True

        if not is_broken:
            for d in distractors:
                why = (d.get("why") or "").strip()
                if why and DISTRACTOR_WHY_FORBIDDEN.search(why):
                    is_broken = True
                    break

        out.append(QuestionEntry(
            uuid=uuid,
            text=text,
            type="select",
            difficulty=difficulty,
            archived=False,
            state="broken" if is_broken else "ready",
            related_article_slug=art_slug,
        ))

    # Process questions in dst_dataset that were not in src_dataset
    for q in dst_dataset:
        uuid = q.get("uuid", "")
        if not uuid or uuid in seen_uuids:
            continue
        rel = q.get("relatedArticleSlug", "")
        art_slug = rel.split("/", 1)[1] if "/" in rel else rel
        is_archived = bool(q.get("archived"))
        if is_archived:
            out.append(QuestionEntry(
                uuid=uuid,
                text=q.get("text", ""),
                type="select",
                difficulty=q.get("difficulty", 1),
                archived=True,
                state="archived",
                related_article_slug=art_slug,
            ))
            continue

        text = q.get("text", "")
        answer = q.get("answer", "")
        distractors = q.get("distractors", [])
        hints = q.get("hints", [])
        difficulty = q.get("difficulty", 1)

        is_broken = False
        if not text.strip() or not answer.strip():
            is_broken = True
        else:
            for d in distractors:
                why = (d.get("why") or "").strip()
                if why and DISTRACTOR_WHY_FORBIDDEN.search(why):
                    is_broken = True
                    break

        out.append(QuestionEntry(
            uuid=uuid,
            text=text,
            type="select",
            difficulty=difficulty,
            archived=False,
            state="broken" if is_broken else "ready",
            related_article_slug=art_slug,
        ))

    return out


@dataclass
class CampaignCell:
    lang: str
    domain: str
    status: str
    articles: int
    expected_articles: int
    questions: int
    short_articles: int


@dataclass
class CampaignDetail:
    slug: str
    name: str
    description: str
    order: int
    domain_slugs: list[str]
    prerequisite_domain_slugs: list[str]
    prerequisite_campaign_slugs: list[str]
    langs: list[str]
    cells: list[CampaignCell]

    def ready_langs(self) -> list[str]:
        return [lang for lang in self.langs if self._lang_status(lang) == "ok"]

    def partial_langs(self) -> list[str]:
        return [lang for lang in self.langs if self._lang_status(lang) == "partial"]

    def _lang_status(self, lang: str) -> str:
        relevant = [c for c in self.cells if c.lang == lang]
        if not relevant:
            return "absent"
        if all(c.status == "ok" for c in relevant):
            return "ok"
        if all(c.status == "absent" for c in relevant):
            return "absent"
        return "partial"


def campaign_detail(
    output_root: Path,
    slug: str,
    langs: list[str],
    cache: Optional[Dict] = None,
) -> CampaignDetail | None:
    campaign = next((c for c in CAMPAIGNS if c["slug"] == slug), None)
    if campaign is None:
        return None

    cells: list[CampaignCell] = []
    for lang in langs:
        for ds in campaign["domainSlugs"]:
            s = domain_summary(output_root, lang, ds, cache=cache)
            cells.append(CampaignCell(
                lang=lang,
                domain=ds,
                status=s.status,
                articles=s.articles,
                expected_articles=s.expected_articles,
                questions=s.questions,
                short_articles=s.short_articles,
            ))

    return CampaignDetail(
        slug=campaign["slug"],
        name=campaign["name"],
        description=campaign.get("description", ""),
        order=campaign.get("order", 0),
        domain_slugs=list(campaign["domainSlugs"]),
        prerequisite_domain_slugs=list(campaign.get("prerequisiteDomainSlugs", [])),
        prerequisite_campaign_slugs=list(campaign.get("prerequisiteCampaignSlugs", [])),
        langs=list(langs),
        cells=cells,
    )


def campaigns_overview(
    output_root: Path,
    langs: list[str],
    cache: Optional[Dict] = None,
) -> list[dict]:
    """Lightweight per-campaign summary for the /campaigns index page."""
    out: list[dict] = []
    for c in sorted(CAMPAIGNS, key=lambda x: x.get("order", 0)):
        detail = campaign_detail(output_root, c["slug"], langs, cache=cache)
        if detail is None:
            continue
        out.append({
            "slug": detail.slug,
            "name": detail.name,
            "description": detail.description,
            "order": detail.order,
            "domain_count": len(detail.domain_slugs),
            "ready": detail.ready_langs(),
            "partial": detail.partial_langs(),
            "total_langs": len(langs),
        })
    return out
