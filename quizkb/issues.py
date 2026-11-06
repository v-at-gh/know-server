"""Read-only issue collection over output/.

Each Issue represents one concrete problem in the current state of the
content tree. Issues are recomputed from disk on every call; there is no
persistence of "this issue was seen before". Persistence — the ack layer —
is separate and only masks issues the user has decided to accept.

Keys are stable across runs for as long as the underlying content is
stable. Each check documents its own key policy.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import (
    SOURCE_LANG, QUESTIONS_PER_ARTICLE, ACKNOWLEDGED_PATH,
)
from .io_utils import load_json_cached
from .parsing import DISTRACTOR_WHY_FORBIDDEN
from .recovery import find_thin_articles
from .translation import audit_missing_questions

MIN_MD_BYTES = 2000


@dataclass
class Issue:
    key: str
    check: str
    severity: str  # "blocker" | "warning"
    title: str
    detail: str
    target: dict
    link: str | None


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


# ---------- individual checks ----------

def check_article_short(output_root: Path, langs: list[str], cache: dict | None = None) -> Iterable[Issue]:
    """Article file below MIN_MD_BYTES.

    Key: article_short:<lang>:<domain>/<slug>  (content-independent)
    """
    for lang in langs:
        knowledge_dir = output_root / "knowledge" / lang
        if not knowledge_dir.is_dir():
            continue
        for md in knowledge_dir.rglob("*.md"):
            try:
                size = md.stat().st_size
            except OSError:
                continue
            if size >= MIN_MD_BYTES:
                continue
            rel = md.relative_to(knowledge_dir)
            parts = rel.parts
            if len(parts) < 2:
                continue
            domain = parts[0]
            slug = md.stem
            yield Issue(
                key=f"article_short:{lang}:{domain}/{slug}",
                check="article_short",
                severity="blocker",
                title=f"article below {MIN_MD_BYTES} bytes",
                detail=f"{size} bytes",
                target={"lang": lang, "domain": domain, "slug": slug},
                link=f"/article/{lang}/{domain}/{slug}",
            )


def check_thin_article(output_root: Path, langs: list[str], cache: dict | None = None) -> Iterable[Issue]:
    """Article has fewer than QUESTIONS_PER_ARTICLE questions.

    Key: thin_article:<lang>:<domain>/<slug>  (content-independent)
    """
    for lang in langs:
        ddir = output_root / "datasets" / lang
        if not ddir.is_dir():
            continue
        domain_slugs: set[str] = set()
        for p in ddir.glob("*.json"):
            if p.name in ("domains.json", "campaigns.json"):
                continue
            domain_slugs.add(p.stem)
        for domain in sorted(domain_slugs):
            for rec in find_thin_articles(output_root, lang=lang, domain_slugs=[domain], cache=cache):
                slug = rec["article_slug"]
                have = rec["have"]
                yield Issue(
                    key=f"thin_article:{lang}:{domain}/{slug}",
                    check="thin_article",
                    severity="warning",
                    title=f"fewer than {QUESTIONS_PER_ARTICLE} questions",
                    detail=f"{have} / {QUESTIONS_PER_ARTICLE}",
                    target={"lang": lang, "domain": domain, "slug": slug},
                    link=f"/article/{lang}/{domain}/{slug}",
                )


def check_missing_uuid(output_root: Path, langs: list[str], cache: dict | None = None) -> Iterable[Issue]:
    """Translation is missing a UUID present in EN.

    Key: missing_uuid:<lang>:<domain>:<uuid>  (content-independent)
    """
    for lang in langs:
        if lang == SOURCE_LANG:
            continue
        ddir = output_root / "datasets" / lang
        if not ddir.is_dir():
            continue
        for p in ddir.glob("*.json"):
            if p.name in ("domains.json", "campaigns.json"):
                continue
            domain = p.stem
            for missing in audit_missing_questions(output_root, lang=lang, domain_slug=domain, cache=cache):
                uuid = missing.get("uuid", "")
                if not uuid:
                    continue
                yield Issue(
                    key=f"missing_uuid:{lang}:{domain}:{uuid}",
                    check="missing_uuid",
                    severity="blocker",
                    title="translation missing a question",
                    detail=f"uuid {uuid[:8]}",
                    target={"lang": lang, "domain": domain, "uuid": uuid},
                    link=f"/question/{uuid}",
                )


def check_why_forbidden(output_root: Path, langs: list[str], cache: dict | None = None) -> Iterable[Issue]:
    """A distractor's `why` matches DISTRACTOR_WHY_FORBIDDEN.

    Key: why_forbidden:<lang>:<uuid8>:<index>:<text_hash>  (content-dependent)
    """
    for lang in langs:
        ddir = output_root / "datasets" / lang
        if not ddir.is_dir():
            continue
        for p in ddir.glob("*.json"):
            if p.name in ("domains.json", "campaigns.json"):
                continue
            domain = p.stem
            questions = load_json_cached(p, [], cache) or []
            for q in questions:
                if q.get("archived"):
                    continue
                uuid = q.get("uuid", "")
                if not uuid:
                    continue
                for i, d in enumerate(q.get("distractors", [])):
                    why = (d.get("why") or "").strip()
                    if not why:
                        continue
                    if DISTRACTOR_WHY_FORBIDDEN.search(why):
                        yield Issue(
                            key=f"why_forbidden:{lang}:{uuid[:8]}:{i}:{_sha8(why)}",
                            check="why_forbidden",
                            severity="warning",
                            title="distractor why references article",
                            detail=f"D{i}: {why[:60]!r}",
                            target={"lang": lang, "domain": domain, "uuid": uuid},
                            link=f"/question/{uuid}",
                        )


# ---------- aggregation ----------

_CHECKS = [
    check_article_short,
    check_thin_article,
    check_missing_uuid,
    check_why_forbidden,
]


def collect_issues(output_root: Path, langs: list[str]) -> list[Issue]:
    """Run all checks and return a deduplicated, stably sorted list."""
    issues: list[Issue] = []
    seen: set[str] = set()
    cache: dict[Path, any] = {}
    for check in _CHECKS:
        for issue in check(output_root, langs, cache):
            if issue.key in seen:
                continue
            seen.add(issue.key)
            issues.append(issue)
    issues.sort(key=lambda i: (
        i.severity != "blocker",
        i.check,
        i.target.get("lang", ""),
        i.target.get("domain", ""),
        i.target.get("slug", "") or i.target.get("uuid", ""),
    ))
    return issues


# ---------- acknowledged (read-write) ----------

_ACKS_CACHE: dict[str, dict] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_acks() -> dict[str, dict]:
    """Read acknowledged.jsonl. Returns {key: {acked_at, note}}.

    Malformed lines are ignored — a corrupt ack never blocks the page.
    """
    global _ACKS_CACHE
    if _ACKS_CACHE is not None:
        return _ACKS_CACHE

    if not ACKNOWLEDGED_PATH.exists():
        _ACKS_CACHE = {}
        return _ACKS_CACHE
    out: dict[str, dict] = {}
    for line in ACKNOWLEDGED_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = obj.get("key")
        if key:
            out[key] = {
                "acked_at": obj.get("acked_at", ""),
                "note": obj.get("note", ""),
            }
    _ACKS_CACHE = out
    return _ACKS_CACHE


def save_acks(acks: dict[str, dict]) -> None:
    """Atomic write via tmp + rename."""
    global _ACKS_CACHE
    ACKNOWLEDGED_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACKNOWLEDGED_PATH.with_suffix(ACKNOWLEDGED_PATH.suffix + ".tmp")
    lines = [
        json.dumps(
            {"key": k, "acked_at": v["acked_at"], "note": v.get("note", "")},
            ensure_ascii=False,
        )
        for k, v in sorted(acks.items())
    ]
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(tmp, ACKNOWLEDGED_PATH)
    _ACKS_CACHE = acks


def add_ack(key: str, note: str = "") -> None:
    acks = load_acks()
    if key in acks:
        return
    acks[key] = {"acked_at": _now_iso(), "note": note}
    save_acks(acks)


def remove_ack(key: str) -> None:
    acks = load_acks()
    if key not in acks:
        return
    acks.pop(key)
    save_acks(acks)


__all__ = [
    "Issue",
    "collect_issues",
    "load_acks", "save_acks", "add_ack", "remove_ack",
]