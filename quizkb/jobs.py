"""Orchestration jobs: chains of lower-level operations.

A job is a single self-contained unit of work that a notebook cell — or the
admin panel, via BackgroundTasks — can invoke. Every job takes `ask`,
`output_root` and `progress` as explicit parameters; nothing is read from
globals or mutated in config.

Jobs must be idempotent in the same sense as the primitives they chain:
re-running on a finished tree does nothing and reports zero work.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

from . import prompts
from .config import (
    SOURCE_LANG, DOMAINS, SYLLABUS_PREDICT, ARTICLE_PREDICT, ARTICLES_PER_DOMAIN
)
from .domain_meta import ensure_meta
from .io_utils import (
    load_json, save_json, new_uuid, strip_fences, ensure_dir
)
from .parsing import parse_syllabus
from .generation import generate_questions_for_domain
from .translation import translate_domain_bundle

AskFn = Callable[..., str | None]
RejectFn = Callable[[str, str, str, str], None]

_DOMAIN_BY_SLUG = {d["slug"]: d for d in DOMAINS}


def sync_config_to_output(output_root, domains, campaigns):
    """Synchronizes DB configuration to output/datasets/ JSON files.
    
    Ensures that `domains.json` and `campaigns.json` in the output datasets folder
    are updated with any newly created CMS entries, allowing the pack builder
    (`make_packs.py`) and release generator to discover and bundle them correctly.
    """
    from .config import SOURCE_LANG
    from .io_utils import load_json, save_json

    # 1. Sync domains.json
    d_path = output_root / "datasets" / SOURCE_LANG / "domains.json"
    d_existing = load_json(d_path, []) or []
    d_slugs = {d["slug"] for d in d_existing}

    for d in domains:
        if d["slug"] not in d_slugs:
            d_existing.append({
                "slug": d["slug"],
                "name": d["name"],
                "description": d.get("description", ""),
                "kind": d.get("kind", "unknown")
            })
    d_existing.sort(key=lambda x: x["slug"])
    save_json(d_path, d_existing)

    # 2. Sync campaigns.json
    c_path = output_root / "datasets" / SOURCE_LANG / "campaigns.json"
    c_existing = load_json(c_path, []) or []
    c_slugs = {c["slug"] for c in c_existing}

    for c in campaigns:
        if c["slug"] not in c_slugs:
            c_existing.append({
                "slug": c["slug"],
                "name": c["name"],
                "description": c.get("description", ""),
                "order": c.get("order", 0),
                "domainSlugs": c.get("domainSlugs", []),
                "prerequisiteDomainSlugs": c.get("prerequisiteDomainSlugs", [])
            })
    c_existing.sort(key=lambda x: x.get("order", 0))
    save_json(c_path, c_existing)

def bootstrap_domain(
    ask,
    *,
    output_root,
    domain_cfg,
    on_progress=None,
    on_reject: RejectFn | None = None,
    verbose=True,
) -> dict:
    """Bootstraps an entire domain from scratch through a 3-step generation pipeline.
    
    1. Syllabus Generation: Prompts the LLM to generate structured curriculum items.
    2. Article Generation: Writes comprehensive markdown knowledge articles for each syllabus topic.
    3. Question Generation: Generates and validates quiz questions linked to the articles.
    """
    slug = domain_cfg["slug"]

    # --- 1. Syllabus ---
    syl_dir = output_root / "syllabus" / SOURCE_LANG
    syl_path = syl_dir / f"{slug}.json"

    if not syl_path.exists() or len(load_json(syl_path, [])) == 0:
        if verbose:
            print(f"[bootstrap] Generating syllabus for {slug}...")
            log.info("bootstrap_syllabus", extra={"slug": slug})
        if on_progress: on_progress("syllabus", 0, 1, slug)

        n_articles = domain_cfg.get("articles_per_domain", ARTICLES_PER_DOMAIN)
        prompt = prompts.SYLLABUS_PROMPT.format(
            n=n_articles, name=domain_cfg["name"], description=domain_cfg["description"]
        )
        raw = ask(prompt, predict=SYLLABUS_PREDICT, label=f"syl:{slug}")

        if raw:
            items = parse_syllabus(raw)
            for it in items:
                it["uuid"] = new_uuid()
            ensure_dir(syl_dir)
            save_json(syl_path, items)

        if on_progress: on_progress("syllabus", 1, 1, slug)

    # --- 2. Articles ---
    syllabus = load_json(syl_path, []) or []
    if not syllabus:
        return {"status": "failed", "reason": "no syllabus generated"}

    if on_progress: on_progress("articles", 0, len(syllabus), "")

    for idx, item in enumerate(syllabus):
        article_key = f"{slug}/{item['slug']}"
        art_dir = output_root / "knowledge" / SOURCE_LANG / slug
        art_path = art_dir / f"{item['slug']}.md"

        if not art_path.exists() or art_path.stat().st_size < 2000:
            if verbose:
                print(f"[bootstrap] Generating article {article_key}...")
                log.info("bootstrap_article", extra={"article_key": article_key})
            prompt = prompts.ARTICLE_PROMPT.format(
                domain_name=domain_cfg["name"], 
                title=item["title"], 
                summary=item["summary"]
            )
            raw = ask(prompt, predict=ARTICLE_PREDICT, label=f"art:{article_key}")

            if raw:
                content = strip_fences(raw)
                ensure_dir(art_dir)
                art_path.write_text(content, encoding="utf-8")
                
        if on_progress: on_progress("articles", idx + 1, len(syllabus), item['slug'])

    _rebuild_index(output_root, SOURCE_LANG, slug)

    # --- 3. Questions ---
    if verbose:
        print(f"[bootstrap] Generating questions for {slug}...")
        log.info("bootstrap_questions", extra={"slug": slug})
    q_result = generate_questions_for_domain(
        ask,
        output_root=output_root,
        domain_slug=slug,
        domain_name=domain_cfg["name"],
        domain_cfg=domain_cfg,
        on_progress=on_progress,
        on_reject=on_reject,
        verbose=verbose
    )
    
    return {"status": "ok", "questions": q_result}

def _rebuild_index(output_root, lang, domain_slug):
    idx_path = output_root / "knowledge" / lang / "index.json"
    index = load_json(idx_path, []) or []
    index = [e for e in index if e["domainSlug"] != domain_slug]

    syllabus = load_json(output_root / "syllabus" / lang / f"{domain_slug}.json", []) or []
    for item in syllabus:
        index.append({
            "slug": f"{domain_slug}/{item['slug']}",
            "uuid": item["uuid"],
            "domainSlug": domain_slug,
            "title": item["title"],
            "description": item["summary"],
            "path": f"{domain_slug}/{item['slug']}.md",
            "order": item["order"],
        })
    index.sort(key=lambda e: (e["domainSlug"], e["order"]))
    save_json(idx_path, index)

def translate_domains(
    ask: AskFn,
    *,
    output_root: Path,
    domains: list[dict] | list[str],
    langs: list[str],
    progress=None,
    on_reject: RejectFn | None = None,
    verbose: bool = True,
    on_progress=None,
) -> dict:
    """Translate every (lang, domain) pair in the cartesian product.

    `domains` — config-shaped dicts ({"slug", ...}) or bare slug strings.
    `langs` — target languages; SOURCE_LANG is filtered out silently.

    Idempotent: articles already present with sufficient size are skipped,
    questions whose UUID is already in the target dataset are skipped.

    Returns {lang: {domain_slug: bundle_result}} where bundle_result has
    the shape produced by translate_domain_bundle:
        {"syllabus_written": bool,
         "articles":  {"written": int, "skipped": int, "failed": int},
         "questions": {"written": int, "skipped": int, "failed": int}}
    """
    slugs = [d["slug"] if isinstance(d, dict) else d for d in domains]
    unknown = [s for s in slugs if s not in _DOMAIN_BY_SLUG]
    if unknown:
        raise ValueError(f"unknown domains: {unknown}")

    target_langs = [lang for lang in langs if lang != SOURCE_LANG]
    from .config import DOMAINS
    domain_dicts = []
    for s in slugs:
        cfg = next((d for d in DOMAINS if d["slug"] == s), None)
        if not cfg:
            raise ValueError(f"Domain {s} not found in config")
        domain_dicts.append(cfg)
    ensure_meta(
        ask,
        output_root=output_root,
        domains=domain_dicts,
        langs=target_langs,
        verbose=verbose
    )
    summary: dict[str, dict] = {}
    for lang in target_langs:
        summary[lang] = {}
        for slug in slugs:
            if verbose:
                print(f"  == {lang}/{slug} ==")
                log.info("translation_bundle", extra={"lang": lang, "domain": slug})
            summary[lang][slug] = translate_domain_bundle(
                ask,
                output_root=output_root,
                lang=lang,
                domain_slug=slug,
                progress=progress,
                on_reject=on_reject,
                verbose=verbose,
                on_progress=on_progress,
            )

    return summary


def generate_questions_for_article(
    ask: AskFn,
    *,
    output_root: Path,
    domain_slug: str,
    lang: str,
    article_key: str,
    count: int,
    on_reject: RejectFn | None = None,
    verbose: bool = True,
    max_attempts: int = 6,
) -> dict:
    """Generate specific numbers of new questions for a specific article in EN first, then translate if needed."""
    from .config import SOURCE_LANG, DOMAINS, SINGLE_Q_PREDICT, TRANSLATION_PREDICT
    from .parsing import parse_questions
    from .pipeline import difficulty_for_order
    from .recovery import _rebuild_index
    from .translation import translate_questions

    article_slug = article_key.split("/", 1)[1] if "/" in article_key else article_key

    # 1. Always generate in SOURCE_LANG (EN) first
    md_path = output_root / "knowledge" / SOURCE_LANG / domain_slug / f"{article_slug}.md"
    if not md_path.exists():
        raise FileNotFoundError(f"EN Article content not found at {md_path}")

    content = md_path.read_text(encoding="utf-8")

    domain_cfg = next((d for d in DOMAINS if d["slug"] == domain_slug), None)
    domain_name = domain_cfg["name"] if domain_cfg else domain_slug

    syllabus = load_json(output_root / "syllabus" / SOURCE_LANG / f"{domain_slug}.json", []) or []
    item = next((it for it in syllabus if it["slug"] == article_slug), None)
    title = item["title"] if item else article_slug
    order = item["order"] if item else 1
    total_syllabus = len(syllabus) or 1

    dataset_path = output_root / "datasets" / SOURCE_LANG / f"{domain_slug}.json"
    dataset = load_json(dataset_path, []) or []

    generated = 0
    needed = count

    if verbose:
        print(f"[generate_questions_for_article] {article_key} (EN): generating {count} questions...")
        log.info("gen_article_questions_start", extra={"article_key": article_key, "count": count})

    while needed > 0:
        prompt = prompts.SINGLE_Q_PROMPT.format(
            title=title,
            domain_name=domain_name,
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
            if any(e.get("text") == q["text"] for e in dataset):
                if on_reject is not None:
                    on_reject(label, "duplicate", prompt, raw)
                continue

            q["uuid"] = new_uuid()
            q["difficulty"] = difficulty_for_order(order, total_syllabus)
            q["relatedArticleSlug"] = article_key

            dataset.append(q)
            save_json(dataset_path, dataset)
            generated += 1
            needed -= 1
            got = True
            if verbose:
                print(f"  +1 EN question generated for {article_key} (attempt {attempt})")
                log.info("en_question_generated", extra={"article_key": article_key, "attempt": attempt})
            break

        if not got:
            if verbose:
                print(f"  !! gave up on generating EN question for {article_key} after max attempts")
                log.warning("gave_up_en_question", extra={"article_key": article_key})
            break

    _rebuild_index(output_root, SOURCE_LANG, domain_slug)

    # 2. If the target language is NOT EN, translate the newly generated questions
    if lang != SOURCE_LANG:
        if verbose:
            print(f"[translate] Translating new questions to {lang}...")
            log.info("translate_new_questions", extra={"lang": lang})
        translate_questions(
            ask,
            output_root=output_root,
            lang=lang,
            domain_slug=domain_slug,
            predict=TRANSLATION_PREDICT,
            on_reject=on_reject,
            verbose=verbose
        )
        _rebuild_index(output_root, lang, domain_slug)

    return {"status": "ok", "generated": generated}


__all__ = ["bootstrap_domain", "sync_config_to_output", "translate_domains", "generate_questions_for_article"]
