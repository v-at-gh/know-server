"""Campaign manifest operations: rebuild from config, translate, verify."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .config import (
    CAMPAIGNS, SOURCE_LANG, TARGET_LANGS, LANG_NAMES,
    DOMAIN_META_PREDICT
)
from .io_utils import load_json, save_json, ensure_dir

AskFn = Callable[..., str | None]


CAMPAIGN_TRANSLATION_PROMPT = """Translate this campaign metadata from English to {lang}.

Output EXACTLY two lines:
NAME: <translated>
DESCRIPTION: <translated>

Input:
NAME: {name}
DESCRIPTION: {description}
"""

_FIELD_RE = re.compile(r"^([A-Z]+):\s*(.+)$")


def _config_entry(c: dict) -> dict:
    return {
        "slug": c["slug"],
        "name": c["name"],
        "description": c["description"],
        "order": c["order"],
        "domainSlugs": c["domainSlugs"],
        "prerequisiteDomainSlugs": c["prerequisiteDomainSlugs"],
    }


def rebuild(output_root: Path, *, verbose: bool = False) -> None:
    """Write output/campaigns/<slug>.json from CAMPAIGNS; merge datasets/<lang>/campaigns.json.

    Existing translations are preserved; missing campaigns are added;
    structural fields (order, domainSlugs, prerequisiteDomainSlugs) are
    updated from config. Newly-created non-source entries get
    needs_translation=True.
    """
    campaigns_dir = output_root / "campaigns"
    ensure_dir(campaigns_dir)

    for c in CAMPAIGNS:
        save_json(campaigns_dir / f"{c['slug']}.json", _config_entry(c))
        if verbose:
            print(f"  wrote campaigns/{c['slug']}.json")

    for lang in [SOURCE_LANG] + TARGET_LANGS:
        path = output_root / "datasets" / lang / "campaigns.json"
        existing = load_json(path, []) or []
        by_slug = {e["slug"]: e for e in existing}

        merged: list[dict] = []
        for c in CAMPAIGNS:
            slug = c["slug"]
            prev = by_slug.get(slug)
            if prev is not None and lang != SOURCE_LANG:
                entry = {
                    "slug": slug,
                    "name": prev.get("name", c["name"]),
                    "description": prev.get("description", c["description"]),
                    "order": c["order"],
                    "domainSlugs": c["domainSlugs"],
                    "prerequisiteDomainSlugs": c["prerequisiteDomainSlugs"],
                    "needs_translation": prev.get("needs_translation", True),
                }
            else:
                entry = _config_entry(c)
                if lang != SOURCE_LANG:
                    entry["needs_translation"] = True
            merged.append(entry)

        merged.sort(key=lambda e: e["order"])
        save_json(path, merged)
        if verbose:
            names = [e["name"] for e in merged]
            print(f"  {lang}: {names}")


def _parse(raw: str) -> tuple[str, str] | None:
    name: str | None = None
    description: str | None = None
    for line in raw.splitlines():
        m = _FIELD_RE.match(line.strip())
        if not m:
            continue
        code, value = m.group(1), m.group(2).strip()
        if code == "NAME":
            name = value
        elif code == "DESCRIPTION":
            description = value
    if name is None or description is None:
        return None
    return name, description


def translate_one(
    ask: AskFn,
    *,
    lang: str,
    slug: str,
    source_name: str,
    source_description: str,
    predict: int,
) -> tuple[str, str] | None:
    """Return (name, description) or None on failure / no-change.

    The caller should treat None as "retry later". A "no change" result
    (model returned the English text verbatim) is not treated as an error
    here — inspect llm.jsonl if the reason matters.
    """
    prompt = CAMPAIGN_TRANSLATION_PROMPT.format(
        lang=LANG_NAMES[lang],
        name=source_name,
        description=source_description,
    )
    raw = ask(prompt, predict=predict, label=f"campaign:{lang}:{slug}")
    if raw is None:
        return None
    parsed = _parse(raw)
    if parsed is None:
        return None
    name, description = parsed
    if name == source_name and description == source_description:
        return None
    return name, description


def translate_pass(
    ask: AskFn,
    *,
    output_root: Path,
    lang: str,
    predict: int = DOMAIN_META_PREDICT,
    verbose: bool = True,
) -> int:
    """Translate every campaign in one language that has needs_translation=True.

    Returns count of campaigns actually updated.
    """
    if lang == SOURCE_LANG:
        return 0

    path = output_root / "datasets" / lang / "campaigns.json"
    entries = load_json(path, []) or []
    translated = 0
    changed = False

    config_by_slug = {c["slug"]: c for c in CAMPAIGNS}

    for entry in entries:
        if not entry.get("needs_translation"):
            continue
        cfg = config_by_slug.get(entry["slug"])
        if cfg is None:
            continue
    
        # НОВОЕ: если текущее значение уже отличается от английского,
        # значит перевод сделан — просто снимаем флаг, LLM не зовём.
        if entry["name"] != cfg["name"] or entry["description"] != cfg["description"]:
            entry["needs_translation"] = False
            changed = True
            if verbose:
                print(f"  {lang}/{entry['slug']}: already translated, flag cleared")
            continue

        # Иначе — переводим как раньше
        result = translate_one(
            ask,
            lang=lang,
            slug=entry["slug"],
            source_name=cfg["name"],              # ← из config
            source_description=cfg["description"],
            predict=predict,
        )
        if result is None:
            if verbose:
                print(f"  {lang}/{entry['slug']}: no translation (check llm.jsonl)")
            continue
        name, description = result
        entry["name"] = name
        entry["description"] = description
        entry["needs_translation"] = False
        translated += 1
        changed = True
        if verbose:
            print(f"  {lang}/{entry['slug']}: {name!r}")

    if changed:
        save_json(path, entries)
    return translated


def validate(output_root: Path) -> list[str]:
    """Return a list of consistency problems. Empty list = all good."""
    problems: list[str] = []
    configured_slugs = {c["slug"] for c in CAMPAIGNS}

    for lang in [SOURCE_LANG] + TARGET_LANGS:
        path = output_root / "datasets" / lang / "campaigns.json"
        entries = load_json(path, []) or []
        entries_by_slug = {e["slug"]: e for e in entries}

        missing = configured_slugs - set(entries_by_slug)
        if missing:
            problems.append(f"{lang}: missing campaigns {sorted(missing)}")

        extra = set(entries_by_slug) - configured_slugs
        if extra:
            problems.append(f"{lang}: unknown campaigns {sorted(extra)}")

        if lang != SOURCE_LANG:
            for slug in configured_slugs & set(entries_by_slug):
                if entries_by_slug[slug].get("needs_translation"):
                    problems.append(f"{lang}/{slug}: needs_translation still set")

    return problems