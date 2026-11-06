"""Recovery operations: find and repair thin or incomplete content."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

from .config import (
    SOURCE_LANG, QUESTIONS_PER_ARTICLE, SINGLE_Q_PREDICT, ARTICLE_PREDICT
)
from .io_utils import load_json, load_json_cached, save_json, new_uuid, strip_fences, ensure_dir
from .parsing import parse_questions
from .pipeline import difficulty_for_order
from . import prompts

AskFn = Callable[..., str | None]
RejectFn = Callable[[str, str, str, str], None]


def find_thin_articles(
    output_root: Path,
    *,
    lang: str,
    domain_slugs: list[str],
    cache: dict | None = None,
    questions_per_article_override: int | None = None,
) -> list[dict]:
    """Read-only. Which articles in `lang` have fewer than
    expected questions linked to them.

    Returns records {domain, article_slug, order, have, need}.
    """
    expected = questions_per_article_override or QUESTIONS_PER_ARTICLE
    records: list[dict] = []
    for ds in domain_slugs:
        p_dataset = output_root / "datasets" / lang / f"{ds}.json"
        dataset = load_json_cached(p_dataset, [], cache) or []
        by_article: dict[str, int] = {}
        for q in dataset:
            if q.get("archived"):
                continue
            rel = q.get("relatedArticleSlug", "")
            if "/" in rel:
                local = rel.split("/", 1)[1]
                by_article[local] = by_article.get(local, 0) + 1

        p_syllabus = output_root / "syllabus" / lang / f"{ds}.json"
        syllabus = load_json_cached(p_syllabus, [], cache) or []
        for item in syllabus:
            have = by_article.get(item["slug"], 0)
            if have < expected:
                records.append({
                    "domain": ds,
                    "article_slug": item["slug"],
                    "order": item.get("order", 0),
                    "have": have,
                    "need": expected - have,
                })
    return records

def recover_thin_articles(
    ask: AskFn,
    *,
    output_root: Path,
    domains: list[dict],
    progress=None,
    on_reject: RejectFn | None = None,
    max_attempts: int = 6,
    verbose: bool = True,
    on_progress=None,
    questions_per_article_override: int | None = None,
) -> dict:
    """Fill missing questions for every thin article in the given domains.

    Uses SINGLE_Q_PROMPT one at a time — batch prompts are unstable on
    complex topics. Idempotent: articles already at expected questions
    are skipped. Always writes to SOURCE_LANG.

    Two-phase design:
      - SCAN    collect work_items (articles that need work) -> total
      - PROCESS iterate work_items, generating questions per article

    The SCAN phase is fast (file reads). It exists so that `on_progress`
    can report a stable `total` and so that kill-at-any-moment leaves
    progress in a consistent, resumable state.

    Returns {"recovered", "given_up", "by_article"}.
    """
    expected = questions_per_article_override or QUESTIONS_PER_ARTICLE
    recovered = 0
    given_up = 0
    by_article: dict[str, int] = {}

    # ---------- Phase 1: SCAN ----------
    work_items: list[dict] = []

    for d in domains:
        ds = d["slug"]
        dataset_path = output_root / "datasets" / SOURCE_LANG / f"{ds}.json"
        dataset = load_json(dataset_path, []) or []
        syllabus = load_json(output_root / "syllabus" / SOURCE_LANG / f"{ds}.json", []) or []

        for item in syllabus:
            article_key = f"{ds}/{item['slug']}"
            existing = [q for q in dataset
                        if q.get("relatedArticleSlug") == article_key and not q.get("archived")]
            need = expected - len(existing)
            if need <= 0:
                continue

            md_path = output_root / "knowledge" / SOURCE_LANG / ds / f"{item['slug']}.md"
            
            # Если статьи нет или она подозрительно короткая — генерируем
            if not md_path.exists() or md_path.stat().st_size < 2000:
                if verbose:
                    print(f"[gen ] article {article_key} (missing or too short)")
                    log.info("gen_article_short", extra={"article_key": article_key})
                prompt = prompts.ARTICLE_PROMPT.format(
                    domain_name=d["name"],
                    title=item["title"],
                    summary=item["summary"],
                )
                raw = ask(prompt, predict=ARTICLE_PREDICT, label=f"art:{article_key}")
                if not raw:
                    if verbose:
                        print(f"  !! failed to generate article {article_key}")
                        log.warning("failed_gen_article", extra={"article_key": article_key})
                    continue # Если не смогли сгенерировать статью, переходим к следующей

                content = strip_fences(raw)
                ensure_dir(md_path.parent)
                md_path.write_text(content, encoding="utf-8")

            content = md_path.read_text(encoding="utf-8")

            work_items.append({
                "domain": d,
                "syllabus": syllabus,
                "dataset": dataset,
                "dataset_path": dataset_path,
                "item": item,
                "article_key": article_key,
                "existing": existing,
                "need": need,
                "content": content,
            })

    total_articles = len(work_items)

    # ---------- Phase 2: PROCESS ----------
    for idx, w in enumerate(work_items):
        domain = w["domain"]
        ds = domain["slug"]
        syllabus = w["syllabus"]
        dataset = w["dataset"]
        dataset_path = w["dataset_path"]
        item = w["item"]
        article_key = w["article_key"]
        existing = w["existing"]
        need = w["need"]
        content = w["content"]

        if on_progress:
            on_progress("recover", idx, total_articles, article_key)

        if verbose:
            print(f"[recover] {article_key}: "
                  f"{len(existing)}/{expected}, need {need}")
            log.info("recover_article", extra={"article_key": article_key, "have": len(existing), "need": need})

        while need > 0:
            prompt = prompts.SINGLE_Q_PROMPT.format(
                title=item["title"],
                domain_name=domain["name"],
                article_content=content[:8000],
            )
            got = False
            for attempt in range(1, max_attempts + 1):
                label = f"1q:{article_key}#{attempt}"
                raw = ask(prompt, predict=SINGLE_Q_PREDICT, label=label)
                if raw is None:
                    if on_reject is not None:
                        on_reject(label, "no_response", prompt, "")
                    continue

                parsed, diagnostics = parse_questions(raw, expected=1)
                if not parsed:
                    if on_reject is not None:
                        if diagnostics:
                            detail = "; ".join(f"Q{d.block_index}: {', '.join(d.errs)}" for d in diagnostics)
                            reason = f"parse_reject: {detail}"
                        else:
                            reason = "parse_reject_empty"
                        on_reject(label, reason, prompt, raw)
                    continue

                q = parsed[0]
                if any(e["text"] == q["text"] for e in existing):
                    if on_reject is not None:
                        on_reject(label, "duplicate", prompt, raw)
                    continue

                q["uuid"] = new_uuid()
                q["difficulty"] = difficulty_for_order(item["order"], len(syllabus))
                q["relatedArticleSlug"] = article_key

                dataset.append(q)
                existing.append(q)
                save_json(dataset_path, dataset)
                recovered += 1
                by_article[article_key] = by_article.get(article_key, 0) + 1
                need -= 1
                got = True
                if verbose:
                    print(f"  +1 ({len(existing)}/{expected}) "
                          f"attempt {attempt}")
                    log.info("question_recovered", extra={"article_key": article_key, "total": len(existing), "attempt": attempt})
                break

            if not got:
                given_up += 1
                if verbose:
                    print(f"  !! gave up on {article_key}: "
                          f"{len(existing)}/{expected}")
                    log.warning("gave_up_recovery", extra={"article_key": article_key, "have": len(existing)})
                break

        if progress is not None and len(existing) >= expected:
            progress.mark(SOURCE_LANG, "questions", article_key)

        if on_progress:
            on_progress("recover", idx + 1, total_articles, article_key)

    # ---------- PHASE 3: REBUILD INDEXES ----------
    touched_domains = {w["domain"]["slug"] for w in work_items}
    for ds in touched_domains:
        _rebuild_index(output_root, SOURCE_LANG, ds)
        if verbose:
            print(f"[index] Rebuilt index for {SOURCE_LANG}/{ds}")
            log.info("rebuilt_index", extra={"lang": SOURCE_LANG, "domain": ds})

    return {"recovered": recovered, "given_up": given_up, "by_article": by_article}

def _rebuild_index(output_root: Path, lang: str, domain_slug: str) -> None:
    idx_path = output_root / "knowledge" / lang / "index.json"
    index = load_json(idx_path, []) or []
    index = [e for e in index if e["domainSlug"] != domain_slug]
    
    syllabus = load_json(output_root / "syllabus" / lang / f"{domain_slug}.json", []) or []
    for item in syllabus:
        md_path = output_root / "knowledge" / lang / domain_slug / f"{item['slug']}.md"
        if not md_path.exists():
            continue
        index.append({
            "slug": f"{domain_slug}/{item['slug']}",
            "uuid": item["uuid"],
            "domainSlug": domain_slug,
            "title": item["title"],
            "description": item["summary"],
            "path": f"{domain_slug}/{item['slug']}.md",
            "order": item.get("order", 0),
        })
    index.sort(key=lambda e: (e["domainSlug"], e["order"]))
    save_json(idx_path, index)
