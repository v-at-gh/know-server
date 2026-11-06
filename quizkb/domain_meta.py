"""Domain metadata: keep datasets/<lang>/domains.json complete.

datasets/<lang>/domains.json holds per-domain name/description. Three
consumers depend on it:
  - release.check_pack refuses to mark a pack ready if the slug is missing
  - state.domain_summary (admin /coverage) reads it for display names
  - install_qap merges it into the Android app's assets

For SOURCE_LANG the name/description come from config verbatim. For target
languages we ask the model. Entries already present are never touched —
we only fill gaps; an existing entry is authoritative.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .config import (
    SOURCE_LANG, TARGET_LANGS, LANG_NAMES, DOMAIN_META_PREDICT,
)
from .io_utils import load_json, save_json
from .prompts import DOMAIN_META_PROMPT

AskFn = Callable[..., str | None]

_FIELD_RE = re.compile(r"^([A-Z]+):\s*(.+)$")


def find_missing(
    output_root: Path,
    *,
    domains: list[dict],
    langs: list[str] | None = None,
) -> list[dict]:
    """Read-only. Which (lang, slug) pairs have no entry in domains.json.

    Returns [{"lang", "slug", "name"}, ...] in config order, per lang.
    """
    target_langs = langs if langs is not None else [SOURCE_LANG] + list(TARGET_LANGS)
    missing: list[dict] = []
    for lang in target_langs:
        path = output_root / "datasets" / lang / "domains.json"
        entries = load_json(path, []) or []
        have = {d.get("slug") for d in entries}
        for domain in domains:
            if domain["slug"] not in have:
                missing.append({
                    "lang": lang,
                    "slug": domain["slug"],
                    "name": domain["name"],
                })
    return missing


def ensure_meta(
    ask: AskFn,
    *,
    output_root: Path,
    domains: list[dict],
    langs: list[str] | None = None,
    predict: int = DOMAIN_META_PREDICT,
    verbose: bool = True,
) -> dict:
    """Ensures domain metadata files (`domains.json`) are complete across all target languages.
    
    CRITICAL PREREQUISITE:
    `ensure_meta` must be executed before translating domains or building release packs.
    Release validation (`release.check_pack`) refuses to mark packs ready if domain metadata
    or descriptions are missing for any target language.
    
    Idempotent: a slug already present in a language is left untouched, while missing
    entries are populated via LLM translation or config fallbacks. Returns
    {"added": int, "skipped": int, "by_lang": {lang: [slugs]}}.
    """
    target_langs = langs if langs is not None else [SOURCE_LANG] + list(TARGET_LANGS)
    added = 0
    skipped = 0
    by_lang: dict[str, list[str]] = {}

    for lang in target_langs:
        path = output_root / "datasets" / lang / "domains.json"
        existing = load_json(path, []) or []
        have = {d.get("slug") for d in existing}
        changed = False

        for domain in domains:
            slug = domain["slug"]
            if slug in have:
                skipped += 1
                continue

            if lang == SOURCE_LANG:
                name = domain["name"]
                description = domain.get("description", "")
            else:
                name, description = _translate_meta(
                    ask, lang=lang, domain=domain,
                    predict=predict, verbose=verbose,
                )

            existing.append({"slug": slug, "name": name, "description": description})
            added += 1
            changed = True
            by_lang.setdefault(lang, []).append(slug)
            if verbose:
                print(f"  + {lang}/{slug}: {name!r}")

        if changed:
            existing.sort(key=lambda d: d.get("slug", ""))
            save_json(path, existing)

    return {"added": added, "skipped": skipped, "by_lang": by_lang}


def _translate_meta(
    ask: AskFn, *, lang: str, domain: dict, predict: int, verbose: bool,
) -> tuple[str, str]:
    """Return (name, description). Falls back to English on any failure.

    Fallback is deliberate: an English name in a target dataset is worse
    than a proper translation, but far better than a missing entry that
    would silently block the release gate.
    """
    prompt = DOMAIN_META_PROMPT.format(
        lang=LANG_NAMES[lang],
        name=domain["name"],
        description=domain.get("description", ""),
    )
    label = f"dommeta:{lang}:{domain['slug']}"
    raw = ask(prompt, predict=predict, label=label)
    if raw is None:
        if verbose:
            print(f"    [{label}] no response, falling back to EN")
        return domain["name"], domain.get("description", "")

    name: str | None = None
    description: str | None = None
    for line in raw.splitlines():
        m = _FIELD_RE.match(line.strip())
        if not m:
            continue
        code, value = m.group(1), m.group(2).strip()
        if code == "NAME" and name is None:
            name = value
        elif code == "DESCRIPTION" and description is None:
            description = value

    if name is None or description is None:
        if verbose:
            print(f"    [{label}] parse failed, falling back to EN")
        return domain["name"], domain.get("description", "")
    return name, description


__all__ = ["ensure_meta", "find_missing"]
