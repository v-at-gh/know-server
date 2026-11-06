"""Generation of questions from scratch for a domain."""
from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

from .config import (
    SOURCE_LANG, QUESTIONS_PER_ARTICLE, DISTRACTORS_PER_QUESTION,
    HINTS_PER_QUESTION, QUESTIONS_PREDICT,
)
from .io_utils import load_json, save_json, new_uuid
from .parsing import parse_questions
from .pipeline import difficulty_for_order
from . import prompts

AskFn = Callable[..., str | None]
RejectFn = Callable[[str, str, str, str], None]


def generate_questions_for_domain(
    ask: AskFn,
    *,
    output_root: Path,
    domain_slug: str,
    domain_name: str,
    domain_cfg: dict | None = None,
    progress=None,
    on_reject: RejectFn | None = None,
    max_attempts: int = 3,
    verbose: bool = True,
    on_progress=None,
) -> dict:
    """Ensure every article in the domain's syllabus has questions according to question_types in domain_cfg.

    Idempotent: skips articles that already have enough questions.
    Uses QUESTIONS_PROMPT, CONSTRUCT_QUESTIONS_PROMPT, and RETRY_QUESTIONS_PROMPT.
    Always writes to SOURCE_LANG (EN).
    """
    syllabus_path = output_root / "syllabus" / SOURCE_LANG / f"{domain_slug}.json"
    syllabus = load_json(syllabus_path, []) or []
    if not syllabus:
        if verbose:
            print(f"[warn] no syllabus found at {syllabus_path}")
            log.warning("no_syllabus_found", extra={"syllabus_path": str(syllabus_path)})
        return {"generated": 0, "skipped": 0, "failed": 0}

    dataset_path = output_root / "datasets" / SOURCE_LANG / f"{domain_slug}.json"
    dataset = load_json(dataset_path, []) or []

    q_types_cfg = domain_cfg.get("question_types", {}) if domain_cfg else {}
    if not q_types_cfg:
        target_select = QUESTIONS_PER_ARTICLE
        target_construct = 0
    else:
        target_select = q_types_cfg.get("select", 0)
        target_construct = q_types_cfg.get("construct", 0)

    total_target_per_article = target_select + target_construct

    # Подсчет существующих вопросов по типам и статьям
    existing_counts_select = {}
    existing_counts_construct = {}
    for q in dataset:
        rel = q.get("relatedArticleSlug", "")
        if "/" in rel:
            k = rel.split("/", 1)[1]
            q_type = q.get("type", "select")
            if q_type == "construct":
                existing_counts_construct[k] = existing_counts_construct.get(k, 0) + 1
            else:
                existing_counts_select[k] = existing_counts_select.get(k, 0) + 1

    generated = 0
    skipped = 0
    failed = 0

    if on_progress: on_progress("questions", 0, len(syllabus), "")

    for idx, item in enumerate(syllabus):
        article_slug = item["slug"]
        article_key = f"{domain_slug}/{article_slug}"
        current_select = existing_counts_select.get(article_slug, 0)
        current_construct = existing_counts_construct.get(article_slug, 0)

        need_select = max(0, target_select - current_select)
        need_construct = max(0, target_construct - current_construct)

        if need_select == 0 and need_construct == 0:
            skipped += 1
            if progress:
                progress.mark(SOURCE_LANG, "questions", article_key)
            if on_progress: on_progress("questions", idx + 1, len(syllabus), article_slug)
            continue

        md_path = output_root / "knowledge" / SOURCE_LANG / domain_slug / f"{article_slug}.md"
        if not md_path.exists():
            if verbose:
                print(f"[skip] no article file for {article_key}")
                log.info("skip_article", extra={"article_key": article_key})
            failed += 1
            if on_progress: on_progress("questions", idx + 1, len(syllabus), article_slug)
            continue

        article_content = md_path.read_text(encoding="utf-8")[:8000]
        if verbose:
            print(f"[gen ] {article_key}: need {need_select} select, {need_construct} construct questions")
            log.info("gen_questions", extra={"article_key": article_key, "need_select": need_select, "need_construct": need_construct})

        success_for_article = 0
        diff = difficulty_for_order(item["order"], len(syllabus))

        # Generate select questions
        if need_select > 0:
            previous_errors = []
            select_generated = 0
            for attempt in range(1, max_attempts + 1):
                if select_generated >= need_select:
                    break
                n_batch = need_select - select_generated
                if attempt == 1 and not previous_errors:
                    prompt = prompts.QUESTIONS_PROMPT.format(
                        n=n_batch,
                        d=DISTRACTORS_PER_QUESTION,
                        h=HINTS_PER_QUESTION,
                        title=item["title"],
                        domain_name=domain_name,
                        article_content=article_content,
                    )
                else:
                    prompt = prompts.RETRY_QUESTIONS_PROMPT.format(
                        n=n_batch,
                        d=DISTRACTORS_PER_QUESTION,
                        title=item["title"],
                        domain_name=domain_name,
                        article_content=article_content,
                        previous_errors="\n".join(previous_errors) or "Output was unparseable.",
                    )

                label = f"q:select:{SOURCE_LANG}:{article_key}#{attempt}"
                raw = ask(prompt, predict=QUESTIONS_PREDICT, label=label)
                if raw is None:
                    previous_errors.append("LLM returned empty response.")
                    if on_reject is not None:
                        on_reject(label, "no_response", prompt, "")
                    continue

                parsed, diagnostics = parse_questions(raw, expected=n_batch)
                if not parsed:
                    previous_errors.append("Output did not contain any valid select questions.")
                    if on_reject is not None:
                        reason = f"parse_reject: {'; '.join(f'Q{d.block_index}: {', '.join(d.errs)}' for d in diagnostics)}" if diagnostics else "parse_reject_empty"
                        on_reject(label, reason, prompt, raw)
                    continue

                for q in parsed:
                    if q.get("type", "select") == "select":
                        q["uuid"] = new_uuid()
                        q["difficulty"] = diff
                        q["relatedArticleSlug"] = article_key
                        dataset.append(q)
                        select_generated += 1
                        success_for_article += 1
                save_json(dataset_path, dataset)

        # Generate construct questions
        if need_construct > 0:
            previous_errors = []
            construct_generated = 0
            for attempt in range(1, max_attempts + 1):
                if construct_generated >= need_construct:
                    break
                n_batch = need_construct - construct_generated
                prompt = prompts.CONSTRUCT_QUESTIONS_PROMPT.format(
                    n=n_batch,
                    h=HINTS_PER_QUESTION,
                    title=item["title"],
                    domain_name=domain_name,
                    article_content=article_content,
                )
                label = f"q:construct:{SOURCE_LANG}:{article_key}#{attempt}"
                raw = ask(prompt, predict=QUESTIONS_PREDICT, label=label)
                if raw is None:
                    previous_errors.append("LLM returned empty response.")
                    if on_reject is not None:
                        on_reject(label, "no_response", prompt, "")
                    continue

                parsed, diagnostics = parse_questions(raw, expected=n_batch)
                if not parsed:
                    previous_errors.append("Output did not contain any valid construct questions.")
                    if on_reject is not None:
                        reason = f"parse_reject: {'; '.join(f'Q{d.block_index}: {', '.join(d.errs)}' for d in diagnostics)}" if diagnostics else "parse_reject_empty"
                        on_reject(label, reason, prompt, raw)
                    continue

                for q in parsed:
                    if q.get("type") == "construct":
                        q["uuid"] = new_uuid()
                        q["difficulty"] = diff
                        q["relatedArticleSlug"] = article_key
                        dataset.append(q)
                        construct_generated += 1
                        success_for_article += 1
                save_json(dataset_path, dataset)

        if success_for_article == 0:
            failed += 1
            if verbose:
                print(f"  !! failed to generate any valid questions for {article_key}")
                log.warning("failed_gen_questions", extra={"article_key": article_key})
        else:
            generated += success_for_article
            if verbose:
                print(f"  +{success_for_article} generated")
                log.info("questions_generated", extra={"count": success_for_article})
            total_current = existing_counts_select.get(article_slug, 0) + existing_counts_construct.get(article_slug, 0) + success_for_article
            if progress and total_current >= total_target_per_article:
                progress.mark(SOURCE_LANG, "questions", article_key)

        if on_progress: on_progress("questions", idx + 1, len(syllabus), article_slug)

    return {"generated": generated, "skipped": skipped, "failed": failed}