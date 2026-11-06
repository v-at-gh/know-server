"""Translation of syllabus, articles and questions from SOURCE_LANG to a target.

All functions are idempotent with respect to existing outputs:
  - syllabus: skipped if target file is present and complete
  - articles: skipped if target .md exists and is at least MIN_ARTICLE_BYTES
  - questions: per-UUID; source UUIDs already present in target are skipped

Callers pass an `ask` callable; the module itself does not know about
OllamaClient, logger, or progress.
"""
from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

from .config import (
    SOURCE_LANG, LANG_NAMES, DOMAINS,
    SYLLABUS_PREDICT, TRANSLATION_PREDICT,
    DISTRACTORS_PER_QUESTION, HINTS_PER_QUESTION,
)
from .io_utils import load_json, load_json_cached, save_json, ensure_dir, strip_fences

AskFn = Callable[..., str | None]
RejectFn = Callable[[str, str, str, str], None]

MIN_ARTICLE_BYTES = 2000

SYLLABUS_TRANSLATION_PROMPT = """Translate the following quiz-topic article metadata from English to {lang}.

For each article, output EXACTLY this block:

--- A{{index}} ---
SLUG: <keep exactly as in the input>
TITLE: <translated title>
SUMMARY: <translated summary>

Rules:
- Keep the SLUG line identical to the input. Slugs are not translated.
- Translate only TITLE and SUMMARY.
- One line per field. No extra text.
- Preserve order and count.

Input:
{input}
"""

ARTICLE_TRANSLATION_PROMPT = """Translate the following Markdown article from English to {lang}.

CRITICAL RULES:
- Preserve ALL Markdown structure: headings (##), lists, **bold**, *italic*, blockquotes, horizontal rules.
- Preserve ALL fenced code blocks (```bash, ```python, ```yaml, ...) — do NOT translate the code inside them.
- Preserve ALL inline code `like this`.
- Preserve ALL LaTeX: $...$ inline, $$...$$ display.
- Preserve URLs, file paths, command names, config keys, and standard English technical terms
  as-is unless a widely accepted translation exists.
- Do NOT add or remove content. Do NOT add a title.
- Keep paragraph structure identical.

Return ONLY the translated Markdown. No preamble, no wrapping fences.

Source:
---
{source}
---
"""

QUESTION_TRANSLATION_PROMPT = """Translate the following quiz questions from English to {lang}.

For each question, output EXACTLY the same block structure as the input,
with markers Q:, A:, D:, W:, H: preserved as-is.

Rules:
- Same number of D: lines, in the same order.
- Same number of W: lines, one after each D:.
- Same number of H: lines.
- One line per field. No multi-line values.
- Translate the content after each marker.
- Keep the same meaning; do not add or remove information.
- Preserve technical terms, command names, file paths, and code in backticks as-is.
- The translated correct answer must NOT appear among translated distractors.
- Distractors must remain distinct in the translation. If two different English
  distractor texts would both translate to the same string, pick a synonym for
  one of them so the four D: lines stay unique.

Input blocks:
{input}
"""

CONSTRUCT_TRANSLATION_PROMPT = """Translate the following CONSTRUCT question from English to {lang}.
Input:
Q: {question}
{tokens_block}
{distractors_block}
{hints_block}
Output EXACTLY:
Q: <translated question>
T: <translated token 1>
...
DT: <translated distractor 1>
...
H: <translated hint 1>
...
Rules:
- The number of T: and DT: lines MUST match the input exactly.
- Translate tokens in the context of the whole sentence.
"""

_BLOCK_RE = re.compile(r"---\s*A(\d+)\s*---")
_FIELD_RE = re.compile(r"^([A-Z]+):\s*(.+)$")
_QBLOCK_RE = re.compile(r"---\s*Q(\d+)\s*---")
_QFIELD_RE = re.compile(r"^([QADWHT]+):\s*(.+)$")


# ---------- syllabus ----------

def translate_syllabus(
    ask: AskFn,
    *,
    output_root: Path,
    lang: str,
    domain_slug: str,
    predict: int,
    progress=None,
    verbose: bool = True,
) -> bool:
    """Translate one (lang, domain) syllabus. Returns True if written."""
    dst = output_root / "syllabus" / lang / f"{domain_slug}.json"
    if dst.exists():
        if progress is not None:
            progress.mark(lang, "syllabus", domain_slug)
        if verbose:
            print(f"    [skip] syllabus/{lang}/{domain_slug}")
            log.info("skip_syllabus", extra={"lang": lang, "domain": domain_slug})
        return False

    src = load_json(output_root / "syllabus" / SOURCE_LANG / f"{domain_slug}.json", []) or []
    if not src:
        if verbose:
            print(f"    [warn] no source syllabus for {domain_slug}")
            log.warning("no_source_syllabus", extra={"domain": domain_slug})
        return False

    lines: list[str] = []
    for it in src:
        lines.append(f"--- A{it['order']} ---")
        lines.append(f"SLUG: {it['slug']}")
        lines.append(f"TITLE: {it['title']}")
        lines.append(f"SUMMARY: {it['summary']}")
    input_text = "\n".join(lines)

    prompt = SYLLABUS_TRANSLATION_PROMPT.format(
        lang=LANG_NAMES[lang], input=input_text,
    )
    raw = ask(prompt, predict=predict, label=f"syl:{lang}:{domain_slug}")
    if raw is None:
        return False

    blocks = _BLOCK_RE.split(raw)
    translated: dict[int, dict[str, str]] = {}
    for i in range(1, len(blocks), 2):
        idx = int(blocks[i])
        body = blocks[i + 1] if i + 1 < len(blocks) else ""
        fields: dict[str, str] = {}
        for line in body.splitlines():
            m = _FIELD_RE.match(line.strip())
            if m:
                fields[m.group(1)] = m.group(2).strip()
        translated[idx] = fields

    result = []
    for it in src:
        t = translated.get(it["order"], {})
        result.append({
            "order": it["order"],
            "slug": it["slug"],
            "uuid": it["uuid"],
            "title": t.get("TITLE", it["title"]),
            "summary": t.get("SUMMARY", it["summary"]),
        })

    save_json(dst, result)
    if progress is not None:
        progress.mark(lang, "syllabus", domain_slug)
    if verbose:
        print(f"    wrote syllabus/{lang}/{domain_slug} ({len(result)} items)")
        log.info("wrote_syllabus", extra={"lang": lang, "domain": domain_slug, "items": len(result)})
    return True


# ---------- articles ----------

def translate_articles(
    ask: AskFn,
    *,
    output_root: Path,
    lang: str,
    domain_slug: str,
    predict: int,
    progress=None,
    on_reject: RejectFn | None = None,
    verbose: bool = True,
    on_progress=None,
) -> dict:
    """Translate all articles for one (lang, domain). Rebuilds index.json.

    Idempotent: existing .md of sufficient size are skipped. Returns counters.
    """
    src_syllabus = load_json(output_root / "syllabus" / SOURCE_LANG / f"{domain_slug}.json", []) or []
    if not src_syllabus:
        if verbose:
            print(f"    [warn] no source syllabus for {domain_slug}")
            log.warning("no_source_syllabus", extra={"domain": domain_slug})
        return {"written": 0, "skipped": 0, "failed": 0}

    written = 0
    skipped = 0
    failed = 0

    for i, item in enumerate(src_syllabus):
        if on_progress:
            on_progress("articles", i, len(src_syllabus), item['slug'])
        article_key = f"{domain_slug}/{item['slug']}"
        dst = output_root / "knowledge" / lang / domain_slug / f"{item['slug']}.md"
        src = output_root / "knowledge" / SOURCE_LANG / domain_slug / f"{item['slug']}.md"
        if not src.exists():
            continue

        if dst.exists() and dst.stat().st_size >= MIN_ARTICLE_BYTES:
            skipped += 1
            if progress is not None:
                progress.mark(lang, "article", article_key)
            continue

        source_text = src.read_text(encoding="utf-8")
        prompt = ARTICLE_TRANSLATION_PROMPT.format(
            lang=LANG_NAMES[lang], source=source_text,
        )
        label = f"art:{lang}:{article_key}"
        raw = ask(prompt, predict=predict, label=label, think=False)
        if raw is None:
            failed += 1
            continue
        content = strip_fences(raw)
        if len(content.encode("utf-8")) < MIN_ARTICLE_BYTES:
            failed += 1
            if on_reject is not None:
                on_reject(label, "too_short", prompt, content)
            if verbose:
                print(f"    [fail] {lang}/{article_key}: response too short")
                log.warning("article_translation_too_short", extra={"lang": lang, "article_key": article_key})
            continue

        ensure_dir(dst.parent)
        dst.write_text(content, encoding="utf-8")
        if progress is not None:
            progress.mark(lang, "article", article_key)
        written += 1
        if verbose:
            print(f"    wrote {lang}/{article_key} ({len(content)} chars)")
            log.info("wrote_article_translation", extra={"lang": lang, "article_key": article_key, "chars": len(content)})

    _rebuild_index(output_root, lang, domain_slug)
    return {"written": written, "skipped": skipped, "failed": failed}


def _rebuild_index(output_root: Path, lang: str, domain_slug: str) -> None:
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


# ---------- questions ----------

def translate_questions(
    ask: AskFn,
    *,
    output_root: Path,
    lang: str,
    domain_slug: str,
    predict: int,
    max_attempts: int = 3,
    on_reject: RejectFn | None = None,
    verbose: bool = True,
    on_progress=None,
) -> dict:
    """Translate all missing question UUIDs for one (lang, domain).

    Idempotent per UUID: questions whose UUID is already in the target
    dataset are skipped.
    """
    src_path = output_root / "datasets" / SOURCE_LANG / f"{domain_slug}.json"
    source = load_json(src_path, []) or []
    if not source:
        return {"written": 0, "skipped": 0, "failed": 0}

    dst_path = output_root / "datasets" / lang / f"{domain_slug}.json"
    target = load_json(dst_path, []) or []
    have = {q["uuid"] for q in target}

    written = 0
    skipped = 0
    failed = 0

    for i, q in enumerate(source):
        if on_progress:
            on_progress("questions", i, len(source), q['uuid'][:8])
        if q["uuid"] in have:
            skipped += 1
            continue

        result = _translate_one_question(
            ask,
            lang=lang,
            source_q=q,
            predict=predict,
            max_attempts=max_attempts,
            on_reject=on_reject,
            verbose=verbose,
        )
        if result is None:
            failed += 1
            continue

        target.append({
            "uuid": q["uuid"],
            "text": result["text"],
            "answer": result["answer"],
            "distractors": result["distractors"],
            "hints": result["hints"],
            "difficulty": q["difficulty"],
            "relatedArticleSlug": q.get("relatedArticleSlug"),
        })
        save_json(dst_path, target)
        have.add(q["uuid"])
        written += 1

    return {"written": written, "skipped": skipped, "failed": failed}


def _render_question_block(q: dict) -> str:
    lines = ["--- Q1 ---", f"Q: {q['text']}", f"A: {q['answer']}"]
    for d in q["distractors"]:
        lines.append(f"D: {d['text']}")
        lines.append(f"W: {d.get('why', '')}")
    for h in q["hints"]:
        lines.append(f"H: {h}")
    return "\n".join(lines)


def _parse_one_question(raw: str) -> tuple[dict | None, tuple[str, ...]]:
    blocks = _QBLOCK_RE.split(raw)
    if len(blocks) < 3:
        return None, ("no question block found",)
    body = blocks[2]

    q_text = answer = None
    pairs: list[list[str]] = []
    hints: list[str] = []
    errs: list[str] = []

    for line in body.splitlines():
        m = _QFIELD_RE.match(line.strip())
        if not m:
            continue
        code, value = m.group(1), m.group(2).strip()
        if code == "Q":
            q_text = value
        elif code == "A":
            answer = value
        elif code == "D":
            pairs.append([value, ""])
        elif code == "W":
            if pairs and pairs[-1][1] == "":
                pairs[-1][1] = value
        elif code == "H":
            hints.append(value)

    seen_texts = set()
    unique_pairs = []
    for d_text, w_text in pairs:
        if d_text not in seen_texts:
            seen_texts.add(d_text)
            unique_pairs.append([d_text, w_text])
    pairs = unique_pairs

    if q_text is None: errs.append("no Q")
    if answer is None: errs.append("no A")
    if len(pairs) < 2: errs.append(f"{len(pairs)} distractors (need >= 2)")
    if any(w == "" for _, w in pairs): errs.append("missing W")
    if len(hints) < 1: errs.append("missing hints")
    if answer is not None and answer in [d for d, _ in pairs]: errs.append("answer appears in distractors")

    if errs:
        return None, tuple(errs)

    return {
        "text": q_text,
        "answer": answer,
        "distractors": [{"text": d, "why": w} for d, w in pairs],
        "hints": hints,
    }, ()


def _translate_one_question(
    ask: AskFn,
    *,
    lang: str,
    source_q: dict,
    predict: int,
    max_attempts: int,
    on_reject: RejectFn | None,
    verbose: bool,
) -> dict | None:
    if source_q.get("type") == "construct":
        tokens_block = "\n".join(f"T: {t['text']}" for t in source_q.get("tokens", []))
        distractors_block = "\n".join(f"DT: {dt['text']}" for dt in source_q.get("distractorTokens", []))
        hints_block = "\n".join(f"H: {h}" for h in source_q.get("hints", []))
        prompt = CONSTRUCT_TRANSLATION_PROMPT.format(
            lang=LANG_NAMES[lang],
            question=source_q["text"],
            tokens_block=tokens_block,
            distractors_block=distractors_block,
            hints_block=hints_block,
        )
        expected_t = len(source_q.get("tokens", []))
        expected_dt = len(source_q.get("distractorTokens", []))
        expected_h = len(source_q.get("hints", []))
        label_base = f"q:construct:{lang}:{source_q['uuid'][:8]}"

        for attempt in range(1, max_attempts + 1):
            label = f"{label_base}#{attempt}"
            raw = ask(prompt, predict=predict, label=label)
            if raw is None:
                if on_reject is not None:
                    on_reject(label, "no_response", prompt, "")
                continue

            parsed, errs = _parse_one_construct_question(raw, expected_t, expected_dt, expected_h)
            if parsed is None:
                if on_reject is not None:
                    detail = ", ".join(errs)
                    on_reject(label, f"parse_reject: {detail}" if detail else "parse_reject", prompt, raw)
                continue
            return parsed
        return None

    block = _render_question_block(source_q)
    prompt = QUESTION_TRANSLATION_PROMPT.format(lang=LANG_NAMES[lang], input=block)

    expected_d = len(source_q["distractors"])
    expected_h = len(source_q["hints"])
    label_base = f"q:{lang}:{source_q['uuid'][:8]}"

    for attempt in range(1, max_attempts + 1):
        label = f"{label_base}#{attempt}"
        raw = ask(prompt, predict=predict, label=label)
        if raw is None:
            if on_reject is not None:
                on_reject(label, "no_response", prompt, "")
            continue

        parsed, errs = _parse_one_question(raw)
        if parsed is None:
            if on_reject is not None:
                detail = ", ".join(errs)
                reason = f"parse_reject: {detail}" if detail else "parse_reject"
                on_reject(label, reason, prompt, raw)
            if verbose:
                print(f"    [parse-reject] {label}")
                log.warning("translation_parse_reject", extra={"label": label})
            continue

        got_d = len(parsed["distractors"])
        if not (2 <= got_d <= 6):
            if on_reject is not None:
                on_reject(
                    label,
                    f"parse_reject: distractor count out of range: {got_d}",
                    prompt, raw,
                )
            if verbose:
                print(f"    [count] {label}: {got_d} distractors, out of range")
                log.warning("translation_distractor_count", extra={"label": label, "got": got_d})
            continue
        if got_d != expected_d and verbose:
            print(f"    [note] {label}: {got_d} distractors vs source {expected_d}")
            log.info("translation_distractor_note", extra={"label": label, "got": got_d, "expected": expected_d})

        if len(parsed["hints"]) != expected_h:
            if on_reject is not None:
                on_reject(
                    label,
                    f"parse_reject: hint count {len(parsed['hints'])} vs {expected_h}",
                    prompt, raw,
                )
            continue

        return parsed
    return None


def _render_construct_question_block(q: dict) -> str:
    lines = ["--- Q1 ---", f"Q: {q['text']}"]
    for t in q.get("tokens", []):
        lines.append(f"T: {t['text']}")
    for dt in q.get("distractorTokens", []):
        lines.append(f"DT: {dt['text']}")
    for h in q.get("hints", []):
        lines.append(f"H: {h}")
    return "\n".join(lines)


def _parse_one_construct_question(raw: str, expected_t: int, expected_dt: int, expected_h: int) -> tuple[dict | None, tuple[str, ...]]:
    blocks = _QBLOCK_RE.split(raw)
    if len(blocks) < 3:
        return None, ("no question block found",)
    body = blocks[2]

    q_text = None
    t_lines: list[str] = []
    dt_lines: list[str] = []
    hints: list[str] = []
    errs: list[str] = []

    for line in body.splitlines():
        m = _QFIELD_RE.match(line.strip())
        if not m:
            continue
        code, value = m.group(1), m.group(2).strip()
        if code == "Q":
            q_text = value
        elif code == "T":
            t_lines.append(value)
        elif code == "DT":
            dt_lines.append(value)
        elif code == "H":
            hints.append(value)

    if q_text is None: errs.append("no Q")
    if len(t_lines) != expected_t: errs.append(f"token count {len(t_lines)} vs {expected_t}")
    if len(dt_lines) != expected_dt: errs.append(f"distractor token count {len(dt_lines)} vs {expected_dt}")
    if len(hints) != expected_h: errs.append(f"hint count {len(hints)} vs {expected_h}")

    if errs:
        return None, tuple(errs)

    tokens = [{"text": t} for t in t_lines]
    distractor_tokens = [{"text": dt} for dt in dt_lines]
    solution = list(range(len(tokens)))
    answer = " ".join(t_lines)
    payload_json = json.dumps({"tokens": tokens, "solution": solution, "distractorTokens": distractor_tokens})

    return {
        "type": "construct",
        "text": q_text,
        "tokens": tokens,
        "solution": solution,
        "distractorTokens": distractor_tokens,
        "answer": answer,
        "payloadJson": payload_json,
        "hints": hints,
    }, ()

# ---------- bundle ----------

_DOMAIN_BY_SLUG = {d["slug"]: d for d in DOMAINS}


def translate_domain_bundle(
    ask: AskFn,
    *,
    output_root: Path,
    lang: str,
    domain_slug: str,
    predict_syllabus: int = SYLLABUS_PREDICT,
    predict_articles: int = TRANSLATION_PREDICT,
    predict_questions: int = TRANSLATION_PREDICT,
    progress=None,
    on_reject: RejectFn | None = None,
    verbose: bool = True,
    on_progress=None,
) -> dict:
    """Translate everything for one (lang, domain): syllabus, articles, questions."""
    if domain_slug not in _DOMAIN_BY_SLUG:
        raise ValueError(f"unknown domain: {domain_slug}")
    if verbose:
        print(f"  == {lang}/{domain_slug} ==")
        log.info("translate_domain_start", extra={"lang": lang, "domain": domain_slug})
    syl_written = translate_syllabus(
        ask,
        output_root=output_root, lang=lang, domain_slug=domain_slug,
        predict=predict_syllabus, progress=progress, verbose=verbose,
    )
    art = translate_articles(
        ask,
        output_root=output_root, lang=lang, domain_slug=domain_slug,
        predict=predict_articles, progress=progress, on_reject=on_reject,
        verbose=verbose, on_progress=on_progress,
    )
    qs = translate_questions(
        ask,
        output_root=output_root, lang=lang, domain_slug=domain_slug,
        predict=predict_questions, on_reject=on_reject,
        verbose=verbose, on_progress=on_progress,
    )
    return {
        "syllabus_written": syl_written,
        "articles": art,
        "questions": qs,
    }

def translate_campaign(
    ask: AskFn,
    *,
    output_root: Path,
    campaign: dict,
    lang: str,
    progress=None,
    on_reject: RejectFn | None = None,
    verbose: bool = True,
) -> dict:
    """Translate an entire campaign into one target language.

    Includes the campaign's metadata (name/description) and every domain
    listed in `campaign["domainSlugs"]` — syllabus, articles, questions.

    `campaign` is a config-shaped dict:
        {"slug": ..., "name": ..., "description": ...,
         "order": ..., "domainSlugs": [...], ...}

    Idempotent: metadata is translated only when needed, domains skip
    already-translated articles and questions. Returns counters.
    """
    if lang == SOURCE_LANG:
        raise ValueError("translate_campaign is for target languages only")

    from . import campaigns as _campaigns

    # Ensure the campaign's per-language metadata entry exists in
    # datasets/<lang>/campaigns.json before attempting to translate it.
    _campaigns.rebuild(output_root, verbose=False)

    meta_translated = _campaigns.translate_pass(
        ask,
        output_root=output_root,
        lang=lang,
        verbose=verbose,
    )

    domains_result: dict[str, dict] = {}
    for ds in campaign["domainSlugs"]:
        if verbose:
            print(f"  == {lang}/{ds} ==")
            log.info("translate_domain_start", extra={"lang": lang, "domain": ds})
        domains_result[ds] = translate_domain_bundle(
            ask,
            output_root=output_root,
            lang=lang,
            domain_slug=ds,
            progress=progress,
            on_reject=on_reject,
            verbose=verbose,
        )

    return {
        "campaign": campaign["slug"],
        "lang": lang,
        "meta_translated": meta_translated,
        "domains": domains_result,
    }

def translate_campaign_into(
    ask: AskFn,
    *,
    output_root: Path,
    campaign: dict,
    langs: list[str],
    progress=None,
    on_reject: RejectFn | None = None,
    verbose: bool = True,
) -> dict:
    results: dict[str, dict] = {}
    for lang in langs:
        if lang == SOURCE_LANG:
            continue
        if verbose:
            print(f"\n=== {campaign['slug']} -> {lang} ===")
            log.info("translate_campaign_start", extra={"campaign": campaign["slug"], "lang": lang})
        results[lang] = translate_campaign(
            ask,
            output_root=output_root,
            campaign=campaign,
            lang=lang,
            progress=progress,
            on_reject=on_reject,
            verbose=verbose,
        )
    return results

# ---------- audit ----------

def audit_missing_questions(
    output_root: Path,
    *,
    lang: str,
    domain_slug: str,
    cache: dict | None = None,
) -> list[dict]:
    """Read-only. Returns source questions whose UUID is absent from target.

    Adds two diagnostic fields to each entry:
      - source_distractor_count / source_hint_count
      - source_why_forbidden_hits  (indices into distractors)
    """
    from .parsing import DISTRACTOR_WHY_FORBIDDEN

    src_path = output_root / "datasets" / SOURCE_LANG / f"{domain_slug}.json"
    src = load_json_cached(src_path, [], cache) or []
    if not src:
        return []

    dst_path = output_root / "datasets" / lang / f"{domain_slug}.json"
    dst = load_json_cached(dst_path, [], cache) or []
    have = {q["uuid"] for q in dst}

    missing: list[dict] = []
    for q in src:
        if q.get("archived") or q["uuid"] in have:
            continue
        why_hits = [
            i for i, d in enumerate(q.get("distractors", []))
            if d.get("why") and DISTRACTOR_WHY_FORBIDDEN.search(d["why"])
        ]
        missing.append({
            **q,
            "source_distractor_count": len(q.get("distractors", [])),
            "source_hint_count": len(q.get("hints", [])),
            "source_why_forbidden_hits": why_hits,
        })
    return missing


def translate_single_question(ask, *, output_root, lang, domain_slug, uuid, on_reject: RejectFn | None = None, verbose=True):
    src_path = output_root / "datasets" / SOURCE_LANG / f"{domain_slug}.json"
    source_qs = load_json(src_path, [])
    source_q = next((q for q in source_qs if q["uuid"] == uuid), None)
    if not source_q:
        raise ValueError(f"Source question {uuid} not found in {SOURCE_LANG}")
        
    dst_path = output_root / "datasets" / lang / f"{domain_slug}.json"
    target_qs = load_json(dst_path, [])
    
    # Remove if it already exists (overwrite)
    target_qs = [q for q in target_qs if q["uuid"] != uuid]
    
    result = _translate_one_question(
        ask, lang=lang, source_q=source_q, predict=TRANSLATION_PREDICT, 
        max_attempts=3, on_reject=on_reject, verbose=verbose
    )
    
    if result:
        target_qs.append({
            "uuid": uuid,
            "text": result["text"],
            "answer": result["answer"],
            "distractors": result["distractors"],
            "hints": result["hints"],
            "difficulty": source_q["difficulty"],
            "relatedArticleSlug": source_q.get("relatedArticleSlug")
        })
        save_json(dst_path, target_qs)
        return True
    return False


__all__ = [
    "translate_syllabus",
    "translate_articles",
    "translate_questions",
    "translate_domain_bundle",
    "translate_single_question",
    "audit_missing_questions",
    "MIN_ARTICLE_BYTES",
]